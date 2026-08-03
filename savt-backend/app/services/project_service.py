import logging
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.cabinet import CabinetRepository
from app.repositories.project import ProjectContactRepository, ProjectRepository, UserProjectRepository
from app.schemas.pagination import PageOut, make_page
from app.utils.warranty import warranty_status as _warranty_status
from app.schemas.project import (
    CabinetProjectPatchIn,
    ProjectCabinetItem,
    ProjectContactOut,
    ProjectCreateIn,
    ProjectListOut,
    ProjectOut,
    ProjectUpdateIn,
)
from app.services import project_folder_service
from app.services.audit_service import AuditLogger
from app.services.project_reconciliation import reconcile_cabinet_access

logger = logging.getLogger(__name__)


class ProjectService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ProjectRepository(session)
        self.cabinet_repo = CabinetRepository(session)
        self.user_project_repo = UserProjectRepository(session)
        self.audit = AuditLogger(session)

    # Создание проекта
    async def create(self, data: ProjectCreateIn, actor_id: int, actor_role: str) -> ProjectOut:
        if data.parent_project_id is not None:
            parent = await self.repo.get_by_id(data.parent_project_id)
            if parent is None or parent.deleted_at is not None:
                raise NotFoundError("Родительский проект не найден")

        unique_code = await self._generate_unique_code()
        project = await self.repo.create(
            name=data.name, unique_code=unique_code, parent_project_id=data.parent_project_id,
        )
        await self.session.flush()
        project.folder_name = project_folder_service.sanitize_folder_name(data.name)
        self.audit.log("project.create", "project", project.id, actor_id, actor_role, {"name": project.name})
        await self.session.commit()
        project_folder_service.schedule_folder_creation(project.id)
        return await self.get(project.id)

    # Получение проекта со всеми его шкафами (админский вид, без ограничений по владению).
    # Фильтры — те же, что и в общем списке ШУ; cabinets в ответе уже отфильтрован ими.
    async def get(
        self,
        project_id: int,
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        warranty_status: str | None = None,
    ) -> ProjectOut:
        project = await self.repo.get_by_id(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")
        cabinets = await self.cabinet_repo.list_by_project(
            project_id, tag_ids=tag_ids,
            has_documents=has_documents, has_photos=has_photos,
            has_users=has_users, has_service_requests=has_service_requests,
            warranty_status=warranty_status,
        )
        return ProjectOut(
            id=project.id,
            name=project.name,
            unique_code=project.unique_code,
            parent_project_id=project.parent_project_id,
            folder_synced_at=project.folder_synced_at,
            cabinets=[
                ProjectCabinetItem(
                    id=c.id, type=c.type, object_number=c.object_number, admin_internal_name=c.admin_internal_name,
                )
                for c in cabinets
            ],
            production_number=project.production_number,
            shipment_planned_at=project.shipment_planned_at,
            shipment_actual_at=project.shipment_actual_at,
            company_name=project.company_name,
            contacts=[
                ProjectContactOut.model_validate(c)
                for c in await ProjectContactRepository(self.session).list_for_project(project.id)
            ],
            warranty_starts_at=project.warranty_starts_at,
            warranty_ends_at=project.warranty_ends_at,
            warranty_status=_warranty_status(project.warranty_ends_at),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    # Обновление проекта
    async def update(self, project_id: int, data: ProjectUpdateIn, actor_id: int, actor_role: str) -> ProjectOut:
        project = await self.repo.get_by_id(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")
        changed = data.model_dump(exclude_unset=True)

        if "parent_project_id" in changed:
            new_parent_id = changed["parent_project_id"]
            if new_parent_id is not None:
                parent = await self.repo.get_by_id(new_parent_id)
                if parent is None or parent.deleted_at is not None:
                    raise NotFoundError("Родительский проект не найден")
                if await self.repo.would_create_cycle(project_id, new_parent_id):
                    raise AlreadyExistsError("Нельзя сделать проект вложенным сам в себя")
            if project.folder_name:
                logger.warning(
                    "Проект %s сменил родителя, но папка уже создана на NAS — "
                    "перенесите её вручную, автоматический перенос не выполняется",
                    project_id,
                )

        name_changed = "name" in changed and changed["name"] != project.name
        for field, value in changed.items():
            setattr(project, field, value)
        self.audit.log("project.update", "project", project_id, actor_id, actor_role, {"fields": list(changed.keys())})
        await self.session.commit()
        await self.session.refresh(project)
        if name_changed:
            project_folder_service.schedule_folder_sync(project_id)
        return await self.get(project_id)

    # Ручной запуск синхронизации папки проекта на NAS (вне расписания и вне
    # проверки is_sync_eligible — явное действие админа должно срабатывать всегда)
    async def sync_folder(self, project_id: int) -> ProjectOut:
        project = await self.repo.get_by_id(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")
        await project_folder_service.sync_project_folder(self.session, project)
        await self.session.refresh(project)
        return await self.get(project_id)

    # Удаление проекта (soft-delete, как у Cabinet)
    async def delete(self, project_id: int, actor_id: int, actor_role: str) -> None:
        project = await self.repo.get_by_id(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")
        self.audit.log("project.delete", "project", project_id, actor_id, actor_role, {"name": project.name})
        await self.repo.soft_delete(project)
        await self.session.commit()

    # Все проекты. Фильтры (tag_ids/has_documents/.../warranty_status) — те же, что
    # и в общем списке ШУ: проект попадает в выдачу, если им соответствует хотя бы
    # один его шкаф. cabinet_count при этом — общее число шкафов в проекте
    # (не отфильтрованное), а не количество совпавших.
    async def list_all(
        self,
        query: str | None = None,
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        warranty_status: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[ProjectListOut]:
        offset = (page - 1) * size
        projects, total = await self.repo.search(
            query=query, tag_ids=tag_ids,
            has_documents=has_documents, has_photos=has_photos,
            has_users=has_users, has_service_requests=has_service_requests,
            warranty_status=warranty_status,
            sort_by=sort_by, sort_order=sort_order, offset=offset, limit=size,
        )
        items = []
        for p in projects:
            cabinets = await self.cabinet_repo.list_by_project(p.id)
            items.append(ProjectListOut(
                id=p.id, name=p.name, unique_code=p.unique_code,
                cabinet_count=len(cabinets), created_at=p.created_at,
                company_name=p.company_name,
                shipment_actual_at=p.shipment_actual_at,
                warranty_ends_at=p.warranty_ends_at,
                warranty_status=_warranty_status(p.warranty_ends_at),
            ))
        return make_page(items, total, page, size)

    # Привязка/отвязка ШУ к проекту — чисто админская группировка, владельцы
    # шкафа (user_cabinets) этим действием не меняются. Если у проекта уже есть
    # участники — каждому из них сразу выдаётся доступ к привязываемому шкафу
    # (в т.ч. если он уже занят посторонним), без дополнительных заявок —
    # админское действие само по себе достаточное разрешение.
    async def set_cabinet_project(
        self, cabinet_id: int, data: CabinetProjectPatchIn, actor_id: int, actor_role: str
    ) -> None:
        cabinet = await self.cabinet_repo.get_by_id(cabinet_id)
        if cabinet is None or cabinet.deleted_at is not None:
            raise NotFoundError("ШУ не найден")

        if data.project_id is not None:
            project = await self.repo.get_by_id(data.project_id)
            if project is None or project.deleted_at is not None:
                raise NotFoundError("Проект не найден")

        cabinet.project_id = data.project_id
        self.audit.log("cabinet.set_project", "cabinet", cabinet_id, actor_id, actor_role,
                       {"project_id": data.project_id})

        created_chats = []
        if data.project_id is not None:
            member_ids = await self.user_project_repo.list_member_ids(data.project_id)
            if member_ids:
                created_chats = await reconcile_cabinet_access(
                    self.session, data.project_id, member_ids, bypass_request=True,
                )

        await self.session.commit()

        if created_chats:
            from app.services.chat_service import chat_summary_dict
            from app.services.realtime_events import publish_chat_created
            for chat in created_chats:
                await publish_chat_created(chat.id, chat_summary_dict(chat))

    # Генерация уникального кода (хранится в кур-коде проекта)
    async def _generate_unique_code(self) -> str:
        while True:
            code = secrets.token_hex(8).upper()
            if await self.repo.find_by_code(code) is None:
                return code
