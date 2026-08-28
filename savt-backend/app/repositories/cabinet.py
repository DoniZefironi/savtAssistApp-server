from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.cabinet_tag import CabinetTag
from app.models.cabinet_user_settings import CabinetUserSettings
from app.models.cabinets import Cabinet
from app.models.cabinet_photo import CabinetPhoto
from app.models.document import Document
from app.models.service_request import ServiceRequest
from app.models.tag import Tag
from app.utils.db import escape_like, fuzzy_condition
from app.models.user import User
from app.models.user_project import UserProject
from app.repositories.base import BaseRepository


# Условия "у ШУ есть теги/документы/фото/пользователи/сервисные заявки/статус гарантии",
# завязанные на Cabinet.id — переиспользуются и в CabinetRepository.search (WHERE по шкафам),
# и в ProjectRepository.search (EXISTS: у проекта есть хотя бы один подходящий шкаф).
def cabinet_match_conditions(
    tag_ids: list[int] | None = None,
    has_documents: bool | None = None,
    has_photos: bool | None = None,
    has_users: bool | None = None,
    has_service_requests: bool | None = None,
    warranty_status: str | None = None,  # "active" | "expiring_soon" | "expired" | "none"
) -> list:
    conditions = []
    if tag_ids:
        tag_subq = (
            select(CabinetTag.cabinet_id)
            .where(CabinetTag.tag_id.in_(tag_ids))
            .distinct()
            .scalar_subquery()
        )
        conditions.append(Cabinet.id.in_(tag_subq))

    if has_documents is not None:
        doc_exists = exists(
            select(Document.id).where(Document.cabinet_id == Cabinet.id)
        )
        conditions.append(doc_exists if has_documents else ~doc_exists)

    if has_photos is not None:
        photo_exists = exists(
            select(CabinetPhoto.id).where(CabinetPhoto.cabinet_id == Cabinet.id)
        )
        conditions.append(photo_exists if has_photos else ~photo_exists)

    if has_users is not None:
        # Доступ выводится из проекта: "у ШУ есть пользователи" значит "у
        # проекта, которому принадлежит ШУ, есть хотя бы один участник"
        user_exists = exists(
            select(UserProject.id).where(UserProject.project_id == Cabinet.project_id)
        )
        conditions.append(user_exists if has_users else ~user_exists)

    if has_service_requests is not None:
        sr_exists = exists(
            select(ServiceRequest.id).where(ServiceRequest.cabinet_id == Cabinet.id)
        )
        conditions.append(sr_exists if has_service_requests else ~sr_exists)

    # Границы совпадают с _warranty_status() (cabinet_service.py) — фильтр по статусу
    # должен возвращать ровно то множество, что подписано этим статусом в ответе.
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=30)
    if warranty_status == "active":
        conditions.append(Cabinet.warranty_ends_at.isnot(None))
        conditions.append(Cabinet.warranty_ends_at >= soon)
    elif warranty_status == "expiring_soon":
        conditions.append(Cabinet.warranty_ends_at.isnot(None))
        conditions.append(Cabinet.warranty_ends_at >= now)
        conditions.append(Cabinet.warranty_ends_at < soon)
    elif warranty_status == "expired":
        conditions.append(Cabinet.warranty_ends_at.isnot(None))
        conditions.append(Cabinet.warranty_ends_at < now)
    elif warranty_status == "none":
        conditions.append(Cabinet.warranty_ends_at.is_(None))

    return conditions


class CabinetRepository(BaseRepository[Cabinet]):
    def __init__(self, session: AsyncSession):
        super().__init__(Cabinet, session)
    # Soft-delete: строка остаётся в БД (история сохраняется),
    # но перестаёт быть видна в поиске/списках/гео и не может быть привязана заново.
    async def soft_delete(self, cabinet: Cabinet) -> None:
        cabinet.deleted_at = datetime.now(timezone.utc)

    # Поиск ШУ по топику MQTT-контроллера — вебхук телеметрии не знает cabinet_id,
    # только топик как есть (см. Cabinet.mqtt_topic)
    async def get_by_mqtt_topic(self, topic: str) -> Cabinet | None:
        result = await self.session.execute(
            select(Cabinet).where(Cabinet.mqtt_topic == topic, Cabinet.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_sim_id(self, sim_id: int) -> Cabinet | None:
        result = await self.session.execute(
            select(Cabinet).where(Cabinet.sim_id == sim_id, Cabinet.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    # Полностью настроенные под телеметрию ШУ (есть и брокер, и топик) — список
    # "куда подключаться", который забирает C#-прокси (см. GET /webhooks/telemetry/targets)
    async def list_telemetry_targets(self) -> list[Cabinet]:
        result = await self.session.execute(
            select(Cabinet).where(
                Cabinet.deleted_at.is_(None),
                Cabinet.mqtt_host.is_not(None),
                Cabinet.mqtt_port.is_not(None),
                Cabinet.mqtt_topic.is_not(None),
            )
        )
        return list(result.scalars().all())
        await self.session.flush()
    # поиск ШУ
    async def search(
        self,
        query: str | None = None,
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        warranty_status: str | None = None,  # "active" | "expired" | "none"
        project_id: int | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Cabinet], int]:
        # Удалённые ШУ не отображаются в общем списке/поиске
        conditions = [Cabinet.deleted_at.is_(None)]
        if query:
            conditions.append(fuzzy_condition(
                query,
                Cabinet.type, Cabinet.object_number, Cabinet.admin_internal_name,
                Cabinet.purpose, Cabinet.description, Cabinet.admin_comment,
            ))
        conditions.extend(cabinet_match_conditions(
            tag_ids=tag_ids, has_documents=has_documents, has_photos=has_photos,
            has_users=has_users, has_service_requests=has_service_requests,
            warranty_status=warranty_status,
        ))

        if project_id is not None:
            conditions.append(Cabinet.project_id == project_id)

        count_stmt = select(func.count(Cabinet.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        sort_column = {
            "type": Cabinet.type,
            "warranty_ends_at": Cabinet.warranty_ends_at,
            "object_number": Cabinet.object_number,
            "admin_internal_name": Cabinet.admin_internal_name,
            "purpose": Cabinet.purpose,
            "created_at": Cabinet.created_at,
        }.get(sort_by, Cabinet.created_at)

        stmt = select(Cabinet)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(sort_column.asc() if sort_order == "asc" else sort_column.desc())
        stmt = stmt.offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    # ШУ, привязанные к проекту. Без фильтров — для подсчёта cabinet_count и
    # каскадных операций по всем ШУ проекта. С фильтрами — для GET
    # /admin/projects/{id}, где cabinets в ответе уже должен быть отфильтрован
    # сервером теми же параметрами, что и общий список ШУ.
    async def list_by_project(
        self,
        project_id: int,
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        warranty_status: str | None = None,
    ) -> list[Cabinet]:
        conditions = [Cabinet.project_id == project_id, Cabinet.deleted_at.is_(None)]
        conditions.extend(cabinet_match_conditions(
            tag_ids=tag_ids, has_documents=has_documents, has_photos=has_photos,
            has_users=has_users, has_service_requests=has_service_requests,
            warranty_status=warranty_status,
        ))
        result = await self.session.execute(select(Cabinet).where(*conditions))
        return list(result.scalars().all())

    # Кол-во живых ШУ сразу по нескольким проектам, одним запросом — вместо
    # N отдельных list_by_project в цикле (см. UserProjectService.list_projects,
    # где раньше это было N+1: по одному запросу на каждый проект пользователя)
    async def count_by_projects(self, project_ids: list[int]) -> dict[int, int]:
        if not project_ids:
            return {}
        result = await self.session.execute(
            select(Cabinet.project_id, func.count(Cabinet.id))
            .where(Cabinet.project_id.in_(project_ids), Cabinet.deleted_at.is_(None))
            .group_by(Cabinet.project_id)
        )
        return dict(result.all())

    async def get_geo(
        self, warranty_status: str | None = None, has_open_requests: bool | None = None,
    ) -> list[tuple]:
        conditions = [Cabinet.deleted_at.is_(None)]
        conditions.extend(cabinet_match_conditions(warranty_status=warranty_status))

        if has_open_requests is not None:
            open_sr_exists = exists(
                select(ServiceRequest.id).where(
                    ServiceRequest.cabinet_id == Cabinet.id,
                    ServiceRequest.status == "open",
                )
            )
            conditions.append(open_sr_exists if has_open_requests else ~open_sr_exists)

        open_sr = exists(
            select(ServiceRequest.id).where(
                ServiceRequest.cabinet_id == Cabinet.id,
                ServiceRequest.status == "open",
            )
        ).label("has_open_requests")
        result = await self.session.execute(
            select(
                Cabinet.id,
                Cabinet.object_number,
                Cabinet.admin_internal_name,
                Cabinet.warranty_ends_at,
                Cabinet.latitude,
                Cabinet.longitude,
                open_sr,
            )
            .where(*conditions)
        )
        return result.all()

    async def get_tags(self, cabinet_ids: list[int]) -> dict[int, list[Tag]]:
        if not cabinet_ids:
            return {}
        result = await self.session.execute(
            select(CabinetTag.cabinet_id, Tag)
            .join(Tag, Tag.id == CabinetTag.tag_id)
            .where(CabinetTag.cabinet_id.in_(cabinet_ids))
        )
        mapping: dict[int, list[Tag]] = {cid: [] for cid in cabinet_ids}
        for cabinet_id, tag in result.all():
            mapping[cabinet_id].append(tag)
        return mapping

    async def set_tags(self, cabinet_id: int, tag_ids: list[int]) -> None:
        await self.session.execute(
            delete(CabinetTag).where(CabinetTag.cabinet_id == cabinet_id)
        )
        for tag_id in tag_ids:
            self.session.add(CabinetTag(cabinet_id=cabinet_id, tag_id=tag_id))
        await self.session.flush()

    # --- Доступ, выведенный из проекта ---
    # Доступа "напрямую к ШУ" больше нет: пользователь видит шкаф тогда и только
    # тогда, когда состоит в user_projects проекта, которому принадлежит этот
    # шкаф (Cabinet.project_id). У ШУ без project_id доступа нет ни у кого,
    # кроме админа/оператора.

    async def list_accessible_for_user(self, user_id: int) -> list[Cabinet]:
        result = await self.session.execute(
            select(Cabinet)
            .join(UserProject, UserProject.project_id == Cabinet.project_id)
            .where(UserProject.user_id == user_id, Cabinet.deleted_at.is_(None))
            .order_by(Cabinet.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_accessible_for_user(self, user_id: int, cabinet_id: int) -> Cabinet | None:
        result = await self.session.execute(
            select(Cabinet)
            .join(UserProject, UserProject.project_id == Cabinet.project_id)
            .where(
                UserProject.user_id == user_id,
                Cabinet.id == cabinet_id,
                Cabinet.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def user_has_access(self, user_id: int, cabinet_id: int) -> bool:
        return await self.get_accessible_for_user(user_id, cabinet_id) is not None

    # Пользователи с доступом к ШУ — участники проекта, которому принадлежит
    # шкаф (пусто, если у ШУ нет проекта). Primary-участник проекта — первым.
    async def list_users_with_access(self, cabinet_id: int) -> list[tuple[User, UserProject]]:
        cabinet = await self.get_by_id(cabinet_id)
        if cabinet is None or cabinet.project_id is None:
            return []
        result = await self.session.execute(
            select(User, UserProject)
            .join(UserProject, UserProject.user_id == User.id)
            .where(UserProject.project_id == cabinet.project_id)
            .order_by(UserProject.is_primary.desc(), UserProject.added_at)
        )
        return result.all()


class CabinetUserSettingsRepository:
    """Личная персонализация ШУ (custom_name/custom_comment) — без семантики
    доступа, см. app/models/cabinet_user_settings.py."""
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user_id: int, cabinet_id: int) -> CabinetUserSettings | None:
        result = await self.session.execute(
            select(CabinetUserSettings).where(
                CabinetUserSettings.user_id == user_id,
                CabinetUserSettings.cabinet_id == cabinet_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_map_for_user(self, user_id: int, cabinet_ids: list[int]) -> dict[int, CabinetUserSettings]:
        if not cabinet_ids:
            return {}
        result = await self.session.execute(
            select(CabinetUserSettings).where(
                CabinetUserSettings.user_id == user_id,
                CabinetUserSettings.cabinet_id.in_(cabinet_ids),
            )
        )
        return {s.cabinet_id: s for s in result.scalars().all()}

    async def get_map_for_cabinet(self, cabinet_id: int, user_ids: list[int]) -> dict[int, CabinetUserSettings]:
        if not user_ids:
            return {}
        result = await self.session.execute(
            select(CabinetUserSettings).where(
                CabinetUserSettings.cabinet_id == cabinet_id,
                CabinetUserSettings.user_id.in_(user_ids),
            )
        )
        return {s.user_id: s for s in result.scalars().all()}

    async def upsert(self, user_id: int, cabinet_id: int, data: dict) -> CabinetUserSettings:
        obj = await self.get(user_id, cabinet_id)
        if obj is None:
            obj = CabinetUserSettings(user_id=user_id, cabinet_id=cabinet_id, **data)
            self.session.add(obj)
        else:
            for k, v in data.items():
                setattr(obj, k, v)
        await self.session.flush()
        return obj


class CabinetRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_pending_addition(self, user_id: int) -> CabinetAdditionRequest | None:
        result = await self.session.execute(
            select(CabinetAdditionRequest).where(
                CabinetAdditionRequest.user_id == user_id,
                CabinetAdditionRequest.status == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def create_addition(
        self, user_id: int, project_id: int, photo_url: str, user_comment: str | None = None
    ) -> CabinetAdditionRequest:
        obj = CabinetAdditionRequest(
            user_id=user_id,
            project_id=project_id,
            photo_url=photo_url,
            user_comment=user_comment,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def get_addition(self, request_id: int) -> CabinetAdditionRequest | None:
        result = await self.session.execute(
            select(CabinetAdditionRequest).where(CabinetAdditionRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    async def list_additions(
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
            conditions.append(CabinetAdditionRequest.status == status)
        if resolved_by_admin_id is not None:
            conditions.append(CabinetAdditionRequest.resolved_by_admin_id == resolved_by_admin_id)
        if search:
            conditions.append(fuzzy_condition(
                search,
                User.full_name, User.phone, User.organization_name,
                CabinetAdditionRequest.user_comment, CabinetAdditionRequest.admin_response,
            ))

        count_stmt = select(func.count(CabinetAdditionRequest.id)).join(User, User.id == CabinetAdditionRequest.user_id)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        _sort_col = {
            "created_at": CabinetAdditionRequest.created_at,
            "resolved_at": CabinetAdditionRequest.resolved_at,
            "status": CabinetAdditionRequest.status,
            "user_full_name": User.full_name,
        }.get(sort_by, CabinetAdditionRequest.created_at)
        order = _sort_col.asc() if sort_order == "asc" else _sort_col.desc()

        stmt = select(CabinetAdditionRequest, User).join(User, User.id == CabinetAdditionRequest.user_id)
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.order_by(order).offset(offset).limit(limit))
        return result.all(), total
