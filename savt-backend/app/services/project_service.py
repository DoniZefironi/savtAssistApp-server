import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.cabinet import CabinetRepository
from app.repositories.project import ProjectContactRepository, ProjectRepository, UserProjectRepository
from app.schemas.pagination import PageOut, make_page
from app.utils.project_year import project_year
from app.utils.warranty import warranty_status as _warranty_status
from app.schemas.project import (
    CabinetProjectPatchIn,
    ProjectCabinetItem,
    ProjectContactOut,
    ProjectListOut,
    ProjectOut,
    ProjectUpdateIn,
)
from app.services import project_folder_service
from app.services.audit_service import AuditLogger

logger = logging.getLogger(__name__)


# Границы диапазонов приходят календарными датами ("2026-08-15" из input[type=date]),
# а в колонках лежит timestamptz. Разворачиваем их в UTC явно и здесь: datetime без
# зоны PostgreSQL истолковал бы в часовом поясе сессии, и граница молча уехала бы на
# несколько часов — на границе суток это меняет выдачу.
def _day_start(day: date | None) -> datetime | None:
    return datetime.combine(day, time.min, tzinfo=timezone.utc) if day else None


def _next_day_start(day: date | None) -> datetime | None:
    """Верхняя граница — начало следующих суток, сравнение строгое. Иначе
    "по 15 августа" означало бы "до 15 августа 00:00" и отсекало бы весь день."""
    start = _day_start(day)
    return start + timedelta(days=1) if start else None


class ProjectService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ProjectRepository(session)
        self.cabinet_repo = CabinetRepository(session)
        self.user_project_repo = UserProjectRepository(session)
        self.audit = AuditLogger(session)

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

    # Синхронизация папки проекта по кнопке. В отличие от ночного прогона
    # выполняется синхронно (админ ждёт результат) и без отбора по гарантии —
    # явное действие человека всегда срабатывает.
    async def sync_folder_now(self, project_id: int) -> dict:
        from app.config import settings
        from app.services import project_folder_service

        project = await self.repo.get_by_id(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")
        if not settings.project_folders_root:
            raise NotFoundError("Синхронизация папок не настроена (PROJECT_FOLDERS_ROOT)")

        from app.repositories.document import DocumentRepository
        doc_repo = DocumentRepository(self.session)

        # Документы считаем и у проекта, и у всех его ШУ — сверка подхватывает
        # файлы из «_Руководство» на обоих уровнях (см. sync_project_folder)
        async def _count_docs() -> int:
            docs, _ = await doc_repo.list_admin(project_id=project_id, limit=10_000)
            total = len(docs)
            for cabinet in await self.cabinet_repo.list_by_project(project_id):
                cabinet_docs, _ = await doc_repo.list_admin(cabinet_id=cabinet.id, limit=10_000)
                total += len(cabinet_docs)
            return total

        docs_before = await _count_docs()
        await project_folder_service.sync_project_folder(self.session, project)
        docs_after = await _count_docs()

        imported = docs_after - docs_before
        return {
            "synced_at": project.folder_synced_at,
            "imported_documents": max(imported, 0),
            "message": (
                f"Подхвачено новых файлов из папки: {imported}" if imported > 0
                else "Папка синхронизирована, новых файлов не найдено"
            ),
        }

    # То же самое, что делает ночной прогон, но по кнопке для всех проектов
    # разом — не нужно кликать sync-folder на каждом проекте отдельно. В отличие
    # от sync_folder_now (один конкретный проект) ограничение по гарантии тут
    # остаётся в силе — иначе на большом архиве кнопка запускала бы полную
    # сверку сотен давно закрытых проектов впустую.
    async def sync_all_folders_now(self) -> dict:
        from app.config import settings
        from app.services import project_folder_service

        if not settings.project_folders_root:
            raise NotFoundError("Синхронизация папок не настроена (PROJECT_FOLDERS_ROOT)")

        stats = await project_folder_service._sync_all_projects(self.session)

        parts = [f"синхронизировано: {stats['synced']}"]
        if stats["relocated"]:
            parts.append(f"перенесено без полной сверки (гарантия истекла): {stats['relocated']}")
        if stats["failed"]:
            parts.append(f"с ошибкой: {stats['failed']}")

        return {
            "total_projects": stats["total"],
            "synced_projects": stats["synced"],
            "relocated_projects": stats["relocated"],
            "failed_projects": stats["failed"],
            "message": f"Проектов всего: {stats['total']} — " + ", ".join(parts),
        }

    # Обновление проекта — только parent_project_id и гарантия, имя не принимается
    # (см. ProjectUpdateIn)
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

        for field, value in changed.items():
            setattr(project, field, value)
        self.audit.log("project.update", "project", project_id, actor_id, actor_role, {"fields": list(changed.keys())})
        await self.session.commit()
        await self.session.refresh(project)
        return await self.get(project_id)

    # Удаление проекта (soft-delete, как у Cabinet). Каскадом удаляются (тем же
    # soft-delete) все ещё живые ШУ проекта, и архивируются чаты — и по этим ШУ
    # (и их заявкам), и собственные чаты проекта. Без этого "удалённый" проект
    # оставался бы полностью живым везде, кроме списка проектов.
    async def delete(self, project_id: int, actor_id: int, actor_role: str) -> None:
        project = await self.repo.get_by_id(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")
        self.audit.log("project.delete", "project", project_id, actor_id, actor_role, {"name": project.name})
        await self.repo.soft_delete(project)

        from app.services.cabinet_service import CabinetService
        from app.services.chat_service import ChatService
        archived_chats = await CabinetService(self.session).delete_for_project(project_id, actor_id, actor_role)
        archived_chats += await ChatService(self.session).archive_project_chats(project_id)

        await self.session.commit()

        if archived_chats:
            from app.services.chat_service import chat_summary_dict
            from app.services.realtime_events import publish_chat_updated
            for chat in archived_chats:
                await publish_chat_updated(chat.id, chat_summary_dict(chat))

    # Все проекты. Два независимых набора фильтров:
    #  - по шкафам проекта (tag_ids/has_documents/... /cabinet_warranty_status) —
    #    проект проходит, если подошёл хотя бы один его шкаф;
    #  - по самому проекту (year/company/shipped/.../warranty_status) — по данным
    #    из карточки сделки и по тому, что привязано к проекту напрямую.
    # Заданные вместе, они складываются по И.
    # cabinet_count в ответе — общее число шкафов в проекте, не количество совпавших.
    async def list_all(
        self,
        query: str | None = None,
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        cabinet_warranty_status: str | None = None,
        year: int | None = None,
        company: str | None = None,
        shipped: bool | None = None,
        shipment_planned_from: date | None = None,
        shipment_planned_to: date | None = None,
        shipment_actual_from: date | None = None,
        shipment_actual_to: date | None = None,
        has_project_documents: bool | None = None,
        has_project_photos: bool | None = None,
        has_project_users: bool | None = None,
        has_contacts: bool | None = None,
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
            cabinet_warranty_status=cabinet_warranty_status,
            year=year, company=company, shipped=shipped,
            shipment_planned_from=_day_start(shipment_planned_from),
            shipment_planned_before=_next_day_start(shipment_planned_to),
            shipment_actual_from=_day_start(shipment_actual_from),
            shipment_actual_before=_next_day_start(shipment_actual_to),
            has_project_documents=has_project_documents,
            has_project_photos=has_project_photos,
            has_project_users=has_project_users,
            has_contacts=has_contacts,
            warranty_status=warranty_status,
            sort_by=sort_by, sort_order=sort_order, offset=offset, limit=size,
        )
        cabinet_counts = await self.cabinet_repo.count_by_projects([p.id for p in projects])
        items = []
        for p in projects:
            items.append(ProjectListOut(
                id=p.id, name=p.name, unique_code=p.unique_code,
                production_number=p.production_number,
                year=project_year(p),
                cabinet_count=cabinet_counts.get(p.id, 0), created_at=p.created_at,
                company_name=p.company_name,
                shipment_planned_at=p.shipment_planned_at,
                shipment_actual_at=p.shipment_actual_at,
                warranty_ends_at=p.warranty_ends_at,
                warranty_status=_warranty_status(p.warranty_ends_at),
            ))
        return make_page(items, total, page, size)

    # Привязка/отвязка ШУ к проекту — доступ к шкафу выводится из проекта, так
    # что привязка сама по себе и есть выдача доступа: все текущие участники
    # проекта сразу видят этот шкаф, без каких-либо дополнительных заявок.
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

        await self.session.commit()

        # Папка ШУ внутри папки проекта — с тем же шаблоном подпапок. Чат ШУ
        # никогда не создаётся автоматически — только сам пользователь, открыв
        # ШУ и нажав на чат (см. ChatService.get_cabinet_chat)
        if data.project_id is not None:
            from app.services import project_folder_service
            project_folder_service.schedule_cabinet_folder(cabinet_id)

    # Участники проекта — единственное место, где теперь хранится доступ к
    # шкафам (Cabinet.project_id + членство здесь). Заменяет прежний
    # "пользователи ШУ" на уровне отдельного шкафа.
    async def list_project_users(self, project_id: int) -> list["ProjectUserOut"]:
        from app.schemas.admin_users import ProjectUserOut

        project = await self.repo.get_by_id(project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")
        rows = await self.user_project_repo.list_members(project_id)
        return [
            ProjectUserOut(
                user_id=user.id,
                full_name=user.full_name,
                phone=user.phone,
                user_type=user.user_type,
                is_primary=up.is_primary,
                added_at=up.added_at,
            )
            for up, user in rows
        ]

    # Убрать пользователя из проекта — теряет доступ разом ко всем его шкафам.
    # Заодно архивирует его чаты по этому проекту и его шкафам (и заявкам) —
    # иначе доступ на запись пропадал бы только для новых чатов, а в уже
    # открытых пользователь мог бы писать и дальше (см. _get_chat_or_403,
    # который проверяет только владение чатом, не текущее членство в проекте).
    async def remove_user_from_project(
        self, project_id: int, user_id: int, reason: str, actor_id: int, actor_role: str
    ) -> None:
        up = await self.user_project_repo.find(user_id, project_id)
        if up is None:
            raise NotFoundError("Пользователь не состоит в этом проекте")
        await self.user_project_repo.delete(up)
        self.audit.log(
            "user_project.remove", "user_project", up.id, actor_id, actor_role,
            {"user_id": user_id, "project_id": project_id, "reason": reason},
        )

        cabinet_ids = [c.id for c in await self.cabinet_repo.list_by_project(project_id)]
        from app.services.chat_service import ChatService
        archived_chats = await ChatService(self.session).archive_user_project_chats(user_id, project_id, cabinet_ids)

        await self.session.commit()

        if archived_chats:
            from app.services.chat_service import chat_summary_dict
            from app.services.realtime_events import publish_chat_updated
            for chat in archived_chats:
                await publish_chat_updated(chat.id, chat_summary_dict(chat))
