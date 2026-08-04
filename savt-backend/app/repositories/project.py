from datetime import datetime, timezone

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinets import Cabinet
from app.models.project import Project
from app.models.project_contact import ProjectContact
from app.models.project_share_request import ProjectShareRequest
from app.models.user import User
from app.models.user_project import UserProject
from app.repositories.base import BaseRepository
from app.repositories.cabinet import cabinet_match_conditions
from app.utils.db import fuzzy_condition


class ProjectRepository(BaseRepository[Project]):
    def __init__(self, session: AsyncSession):
        super().__init__(Project, session)

    # поиск кода (удалённые проекты не находятся — их код больше нельзя использовать)
    async def find_by_code(self, unique_code: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(
                Project.unique_code == unique_code,
                Project.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    # Поиск по открытому номеру проекта из Bitrix (для идемпотентности вебхука
    # сделок — см. app/services/bitrix_webhook_service.py handle_deal_event)
    async def find_by_production_number(self, production_number: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(
                Project.production_number == production_number,
                Project.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def soft_delete(self, project: Project) -> None:
        project.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()

    # Сколько активных проектов претендуют на папку с таким именем. Нужно при
    # переносе папок в годовые: одноимённую папку в общем корне нельзя молча
    # присвоить одному из них — файлы уехали бы к чужому проекту.
    async def count_active_by_folder_name(self, folder_name: str) -> int:
        result = await self.session.execute(
            select(func.count(Project.id)).where(
                Project.folder_name == folder_name,
                Project.deleted_at.is_(None),
            )
        )
        return result.scalar() or 0

    async def list_all_active(self) -> list[Project]:
        result = await self.session.execute(
            select(Project).where(Project.deleted_at.is_(None))
        )
        return list(result.scalars().all())

    # Цепочка предков от корня до непосредственного родителя (сам project_id не включается).
    # Защищено от зацикливания на случай испорченных данных.
    async def get_ancestors(self, project_id: int) -> list[Project]:
        ancestors: list[Project] = []
        seen = {project_id}
        current = await self.get_by_id(project_id)
        while current is not None and current.parent_project_id is not None:
            if current.parent_project_id in seen:
                break
            parent = await self.get_by_id(current.parent_project_id)
            if parent is None:
                break
            ancestors.append(parent)
            seen.add(parent.id)
            current = parent
        return list(reversed(ancestors))

    # Проверка, что project_id не станет сам себе потомком, если ему назначить
    # родителем new_parent_id (new_parent_id не должен быть project_id, и project_id
    # не должен быть уже предком new_parent_id)
    async def would_create_cycle(self, project_id: int, new_parent_id: int) -> bool:
        if new_parent_id == project_id:
            return True
        ancestors = await self.get_ancestors(new_parent_id)
        return any(a.id == project_id for a in ancestors)

    # Bulk-подстановка названий проектов в карточки ШУ (CabinetOut/CabinetListOut/...) —
    # включает и удалённые проекты (deleted_at не фильтруется), т.к. cabinet.project_id
    # мог остаться проставленным на уже мягко удалённый проект.
    async def get_names_by_ids(self, project_ids: list[int]) -> dict[int, str]:
        if not project_ids:
            return {}
        result = await self.session.execute(
            select(Project.id, Project.name).where(Project.id.in_(project_ids))
        )
        return {pid: name for pid, name in result.all()}

    async def search(
        self,
        query: str | None = None,
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        warranty_status: str | None = None,  # "active" | "expired" | "none"
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Project], int]:
        conditions = [Project.deleted_at.is_(None)]
        if query:
            conditions.append(fuzzy_condition(query, Project.name))

        # Проект попадает в выдачу, если условиям соответствует хотя бы один его шкаф
        cabinet_conditions = cabinet_match_conditions(
            tag_ids=tag_ids, has_documents=has_documents, has_photos=has_photos,
            has_users=has_users, has_service_requests=has_service_requests,
            warranty_status=warranty_status,
        )
        if cabinet_conditions:
            conditions.append(exists(
                select(Cabinet.id).where(
                    Cabinet.project_id == Project.id,
                    Cabinet.deleted_at.is_(None),
                    *cabinet_conditions,
                )
            ))

        count_stmt = select(func.count(Project.id)).where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        sort_column = {
            "name": Project.name,
            "created_at": Project.created_at,
        }.get(sort_by, Project.created_at)

        stmt = (
            select(Project)
            .where(*conditions)
            .order_by(sort_column.asc() if sort_order == "asc" else sort_column.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total


class ProjectContactRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_project(self, project_id: int) -> list[ProjectContact]:
        result = await self.session.execute(
            select(ProjectContact)
            .where(ProjectContact.project_id == project_id)
            .order_by(ProjectContact.sort_order, ProjectContact.id)
        )
        return list(result.scalars().all())

    async def list_for_projects(self, project_ids: list[int]) -> dict[int, list[ProjectContact]]:
        if not project_ids:
            return {}
        result = await self.session.execute(
            select(ProjectContact)
            .where(ProjectContact.project_id.in_(project_ids))
            .order_by(ProjectContact.sort_order, ProjectContact.id)
        )
        mapping: dict[int, list[ProjectContact]] = {pid: [] for pid in project_ids}
        for contact in result.scalars().all():
            mapping[contact.project_id].append(contact)
        return mapping

    async def replace_for_project(self, project_id: int, contacts: list[dict]) -> None:
        """Приводит набор контактов проекта к присланному из Bitrix.

        Существующие обновляются на месте (по bitrix_contact_id), новые
        добавляются, пропавшие из сделки удаляются. Не пересоздаём набор целиком,
        чтобы не менять id записей на каждом обновлении сделки."""
        existing = {c.bitrix_contact_id: c for c in await self.list_for_project(project_id)}
        seen: set[str] = set()

        for data in contacts:
            contact_id = str(data["bitrix_contact_id"])
            seen.add(contact_id)
            obj = existing.get(contact_id)
            if obj is None:
                obj = ProjectContact(project_id=project_id, bitrix_contact_id=contact_id)
                self.session.add(obj)
            obj.full_name = data.get("full_name")
            obj.post = data.get("post")
            obj.phones = data.get("phones") or []
            obj.emails = data.get("emails") or []
            obj.sort_order = int(data.get("sort_order") or 0)

        for contact_id, obj in existing.items():
            if contact_id not in seen:
                await self.session.delete(obj)

        await self.session.commit()


class UserProjectRepository(BaseRepository[UserProject]):
    def __init__(self, session: AsyncSession):
        super().__init__(UserProject, session)

    async def find(self, user_id: int, project_id: int) -> UserProject | None:
        result = await self.session.execute(
            select(UserProject).where(
                UserProject.user_id == user_id,
                UserProject.project_id == project_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list:
        result = await self.session.execute(
            select(UserProject, Project)
            .join(Project, Project.id == UserProject.project_id)
            .where(UserProject.user_id == user_id)
            .order_by(UserProject.is_primary.desc(), UserProject.added_at.desc())
        )
        return result.all()

    async def get_with_project(self, user_id: int, project_id: int):
        result = await self.session.execute(
            select(UserProject, Project)
            .join(Project, Project.id == UserProject.project_id)
            .where(
                UserProject.user_id == user_id,
                UserProject.project_id == project_id,
            )
        )
        return result.one_or_none()

    async def has_primary(self, project_id: int) -> bool:
        result = await self.session.execute(
            select(UserProject).where(
                UserProject.project_id == project_id,
                UserProject.is_primary == True,
            )
        )
        return result.scalar_one_or_none() is not None

    # текущие участники проекта, primary всегда первым — важно для
    # реконсиляции доступа к шкафам (см. ProjectService._reconcile_cabinet_access)
    async def list_member_ids(self, project_id: int) -> list[int]:
        result = await self.session.execute(
            select(UserProject.user_id)
            .where(UserProject.project_id == project_id)
            .order_by(UserProject.is_primary.desc(), UserProject.added_at.asc())
        )
        return list(result.scalars().all())


class ProjectRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_pending_share(self, user_id: int, project_id: int) -> ProjectShareRequest | None:
        result = await self.session.execute(
            select(ProjectShareRequest).where(
                ProjectShareRequest.user_id == user_id,
                ProjectShareRequest.project_id == project_id,
                ProjectShareRequest.status == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def create_share(
        self, user_id: int, project_id: int, user_comment: str | None = None
    ) -> ProjectShareRequest:
        obj = ProjectShareRequest(
            user_id=user_id,
            project_id=project_id,
            user_comment=user_comment,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def get_share(self, request_id: int) -> ProjectShareRequest | None:
        result = await self.session.execute(
            select(ProjectShareRequest).where(ProjectShareRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    async def list_shares(
        self,
        status: str | None = None,
        resolved_by_admin_id: int | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list, int]:
        conditions = []
        if status:
            conditions.append(ProjectShareRequest.status == status)
        if resolved_by_admin_id is not None:
            conditions.append(ProjectShareRequest.resolved_by_admin_id == resolved_by_admin_id)
        if search:
            conditions.append(fuzzy_condition(
                search,
                User.full_name, User.phone, User.organization_name,
                Project.name,
                ProjectShareRequest.user_comment, ProjectShareRequest.admin_response,
            ))

        count_stmt = (
            select(func.count(ProjectShareRequest.id))
            .join(User, User.id == ProjectShareRequest.user_id)
            .join(Project, Project.id == ProjectShareRequest.project_id)
        )
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        _sort_col = {
            "created_at": ProjectShareRequest.created_at,
            "resolved_at": ProjectShareRequest.resolved_at,
            "status": ProjectShareRequest.status,
            "user_full_name": User.full_name,
            "project_name": Project.name,
        }.get(sort_by, ProjectShareRequest.created_at)
        order = _sort_col.asc() if sort_order == "asc" else _sort_col.desc()

        stmt = (
            select(ProjectShareRequest, User, Project)
            .join(User, User.id == ProjectShareRequest.user_id)
            .join(Project, Project.id == ProjectShareRequest.project_id)
        )
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.order_by(order).offset(offset).limit(limit))
        return result.all(), total
