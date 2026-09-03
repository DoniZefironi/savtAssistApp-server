from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, String, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_photo import CabinetPhoto
from app.models.cabinets import Cabinet
from app.models.document import Document
from app.models.project import Project
from app.models.project_contact import ProjectContact
from app.models.project_share_request import ProjectShareRequest
from app.models.user import User
from app.models.user_project import UserProject
from app.repositories.base import BaseRepository
from app.repositories.cabinet import cabinet_match_conditions
from app.utils.db import fuzzy_condition, match_score


def project_year_expr():
    """Год проекта: из производственного номера ("26_170" → 2026), иначе по дате
    создания записи. Ровно то же правило, по которому раскладываются папки на NAS
    (project_folder_service._year_folder_name) — фильтр по году и папка на диске
    не должны расходиться.

    substring() с регулярным выражением возвращает NULL, если номер не начинается
    с двух цифр, поэтому cast к числу безопасен: нецифровой номер не уронит запрос,
    а просто отдаст управление coalesce."""
    from_number = 2000 + cast(
        func.substring(Project.production_number, r"^(\d{2})"), Integer
    )
    return func.coalesce(
        from_number, cast(func.extract("year", Project.created_at), Integer)
    )


def _warranty_conditions(column, status: str) -> list:
    """Границы совпадают с utils.warranty.warranty_status() — фильтр обязан
    возвращать ровно то множество, что подписано этим статусом в ответе."""
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=30)
    if status == "active":
        return [column.isnot(None), column >= soon]
    if status == "expiring_soon":
        return [column.isnot(None), column >= now, column < soon]
    if status == "expired":
        return [column.isnot(None), column < now]
    if status == "none":
        return [column.is_(None)]
    return []


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

    # Поиск по открытому номеру проекта из Bitrix, включая мягко удалённые —
    # нужен вебхуку сделок, чтобы воскресить проект, если его удалили в
    # приложении, а сделка в Bitrix ещё жива (см. upsert_project_from_deal,
    # app/services/bitrix_webhook_service.py handle_deal_event).
    async def find_by_production_number_any(self, production_number: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(Project.production_number == production_number)
        )
        return result.scalar_one_or_none()

    # Поиск по ID сделки Bitrix, включая мягко удалённые — основной ключ
    # идемпотентности вебхука сделок (см. upsert_project_from_deal). В отличие
    # от find_by_production_number_any однозначно определяет "та же самая
    # сделка", а не "проект с таким же номером" — двух сделок с одним ID не бывает.
    async def find_by_bitrix_deal_id(self, bitrix_deal_id: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(Project.bitrix_deal_id == bitrix_deal_id)
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
        # --- фильтры по шкафам проекта: проект проходит, если подошёл хотя бы один ---
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        cabinet_warranty_status: str | None = None,
        # --- фильтры по самому проекту ---
        year: int | None = None,
        company: str | None = None,
        shipped: bool | None = None,
        # *_before — граница исключающая (начало следующих суток), см.
        # project_service._next_day_start
        shipment_planned_from: datetime | None = None,
        shipment_planned_before: datetime | None = None,
        shipment_actual_from: datetime | None = None,
        shipment_actual_before: datetime | None = None,
        has_project_documents: bool | None = None,
        has_project_photos: bool | None = None,
        has_project_users: bool | None = None,
        has_contacts: bool | None = None,
        warranty_status: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Project], int]:
        conditions = [Project.deleted_at.is_(None)]

        if query:
            # Ищем и по карточке проекта, и по контактным лицам заказчика: искать
            # проект по фамилии или телефону человека из сделки — обычный сценарий,
            # а название и номер оператор помнит не всегда
            contact_match = exists(
                select(ProjectContact.id).where(
                    ProjectContact.project_id == Project.id,
                    fuzzy_condition(
                        query,
                        ProjectContact.full_name, ProjectContact.post,
                        # телефоны и почты — JSON-списки; по тексту списка
                        # находится и "+375291112233", и "ivanov@"
                        cast(ProjectContact.phones, String),
                        cast(ProjectContact.emails, String),
                    ),
                )
            )
            conditions.append(or_(
                fuzzy_condition(
                    query,
                    Project.name, Project.production_number, Project.company_name,
                ),
                contact_match,
            ))

            # Релевантность — отдельно от самого отбора выше (fuzzy_condition
            # только решает, попадает ли строка в выдачу вообще). Название и
            # номер проекта весят больше совпадения в компании, а то — больше
            # совпадения по контакту: спросили "100", нашёлся и проект с таким
            # номером, и проект, у чьего контакта "100" затесалось в телефоне —
            # первый должен быть выше, а не когда повезёт по sort_by
            contact_score = (
                select(func.max(func.greatest(
                    match_score(query, ProjectContact.full_name, 0.3),
                    match_score(query, ProjectContact.post, 0.3),
                    match_score(query, cast(ProjectContact.phones, String), 0.3),
                    match_score(query, cast(ProjectContact.emails, String), 0.3),
                )))
                .where(ProjectContact.project_id == Project.id)
                .correlate(Project)
                .scalar_subquery()
            )
            relevance = func.greatest(
                match_score(query, Project.name, 1.0),
                match_score(query, Project.production_number, 1.0),
                match_score(query, Project.company_name, 0.6),
                func.coalesce(contact_score, 0.0),
            )
        else:
            relevance = None

        if year is not None:
            conditions.append(project_year_expr() == year)
        if company:
            conditions.append(fuzzy_condition(company, Project.company_name))
        if shipped is not None:
            conditions.append(
                Project.shipment_actual_at.isnot(None) if shipped
                else Project.shipment_actual_at.is_(None)
            )
        for column, bound, is_lower in (
            (Project.shipment_planned_at, shipment_planned_from, True),
            (Project.shipment_planned_at, shipment_planned_before, False),
            (Project.shipment_actual_at, shipment_actual_from, True),
            (Project.shipment_actual_at, shipment_actual_before, False),
        ):
            if bound is not None:
                conditions.append(column >= bound if is_lower else column < bound)

        for flag, subquery in (
            (has_project_documents, select(Document.id).where(Document.project_id == Project.id)),
            (has_project_photos, select(CabinetPhoto.id).where(CabinetPhoto.project_id == Project.id)),
            (has_project_users, select(UserProject.id).where(UserProject.project_id == Project.id)),
            (has_contacts, select(ProjectContact.id).where(ProjectContact.project_id == Project.id)),
        ):
            if flag is not None:
                condition = exists(subquery)
                conditions.append(condition if flag else ~condition)

        if warranty_status:
            conditions.extend(_warranty_conditions(Project.warranty_ends_at, warranty_status))

        # Проект попадает в выдачу, если условиям соответствует хотя бы один его шкаф
        cabinet_conditions = cabinet_match_conditions(
            tag_ids=tag_ids, has_documents=has_documents, has_photos=has_photos,
            has_users=has_users, has_service_requests=has_service_requests,
            warranty_status=cabinet_warranty_status,
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

        cabinet_count = (
            select(func.count(Cabinet.id))
            .where(Cabinet.project_id == Project.id, Cabinet.deleted_at.is_(None))
            .scalar_subquery()
        )
        sort_column = {
            "name": Project.name,
            "created_at": Project.created_at,
            "production_number": Project.production_number,
            "year": project_year_expr(),
            "company_name": Project.company_name,
            "shipment_planned_at": Project.shipment_planned_at,
            "shipment_actual_at": Project.shipment_actual_at,
            "warranty_ends_at": Project.warranty_ends_at,
            "cabinet_count": cabinet_count,
        }.get(sort_by, Project.created_at)

        # Пустое значение — всегда в конце, в обе стороны сортировки: проект без
        # даты отгрузки не должен занимать верх списка, отсортированного по ней
        order = sort_column.asc() if sort_order == "asc" else sort_column.desc()
        # Пока есть поисковый запрос — релевантность впереди выбранной
        # сортировки, а не наоборот (как в обычном поиске): sort_by решает
        # только порядок среди одинаково релевантных строк, не перебивает его
        order_by = (relevance.desc(), order.nulls_last(), Project.id.desc()) if relevance is not None \
            else (order.nulls_last(), Project.id.desc())
        stmt = (
            select(Project)
            .where(*conditions)
            .order_by(*order_by)
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
        # deleted_at: soft-delete проекта (ProjectService.delete) не трогает
        # строки UserProject — без этого фильтра удалённый проект оставался бы
        # виден в /projects тому, кто в нём состоял, хотя админам он уже не
        # виден вовсе (GET /admin/projects фильтрует deleted_at так же)
        result = await self.session.execute(
            select(UserProject, Project)
            .join(Project, Project.id == UserProject.project_id)
            .where(UserProject.user_id == user_id, Project.deleted_at.is_(None))
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
                Project.deleted_at.is_(None),
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

    # Участники проекта вместе с самим пользователем — для админского списка
    # "участники проекта" (GET /admin/projects/{id}/users)
    async def list_members(self, project_id: int) -> list[tuple[UserProject, User]]:
        result = await self.session.execute(
            select(UserProject, User)
            .join(User, User.id == UserProject.user_id)
            .where(UserProject.project_id == project_id)
            .order_by(UserProject.is_primary.desc(), UserProject.added_at)
        )
        return result.all()


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
