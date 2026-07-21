import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.repositories.cabinet import CabinetRepository
from app.repositories.tag import TagRepository
from app.schemas.cabinet import CabinetCreateIn, CabinetGeoItem, CabinetListOut, CabinetOut, CabinetUpdateIn
from app.schemas.tags import TagOut
from app.schemas.pagination import PageOut, make_page
from app.services.audit_service import AuditLogger


async def _resolve_type(session: AsyncSession, raw_type: str) -> str:
    """Нормализует тип ШУ в нижний регистр и создаёт тег cabinet_type если его нет."""
    normalized = raw_type.strip().lower()
    if not normalized:
        return raw_type
    repo = TagRepository(session)
    existing = await repo.get_by_name_and_scope(normalized, "cabinet_type")
    if existing is None:
        await repo.create(normalized, "cabinet_type")
    return normalized


def _warranty_status(ends_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    if ends_at < now:
        return "expired"
    if ends_at < now + timedelta(days=30):
        return "expiring_soon"
    return "active"


class CabinetService:
    def __init__(self, session: AsyncSession):
        self.repo = CabinetRepository(session)
        self.session = session
        self.audit = AuditLogger(session)

    # Создание ШУ
    async def create(self, data: CabinetCreateIn, actor_id: int, actor_role: str) -> CabinetOut:
        unique_code = await self._generate_unique_code()
        cabinet = await self.repo.create(
            unique_code=unique_code,
            type=await _resolve_type(self.session, data.type),
            object_number=data.object_number,
            description=data.description,
            warranty_starts_at=data.warranty_starts_at,
            warranty_ends_at=data.warranty_ends_at,
            admin_internal_name=data.admin_internal_name,
            admin_comment=data.admin_comment,
            purpose=data.purpose,
            latitude=data.latitude,
            longitude=data.longitude,
        )
        await self.session.flush()
        self.audit.log("cabinet.create", "cabinet", cabinet.id, actor_id, actor_role,
                       {"object_number": cabinet.object_number, "type": cabinet.type})
        await self.session.commit()
        return await self.get(cabinet.id)

    # Получение ШУ с тегами
    async def get(self, cabinet_id: int) -> CabinetOut:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")
        tags_map = await self.repo.get_tags([cabinet_id])
        return CabinetOut(
            id=cabinet.id,
            unique_code=cabinet.unique_code,
            type=cabinet.type,
            object_number=cabinet.object_number,
            description=cabinet.description,
            warranty_starts_at=cabinet.warranty_starts_at,
            warranty_ends_at=cabinet.warranty_ends_at,
            admin_internal_name=cabinet.admin_internal_name,
            admin_comment=cabinet.admin_comment,
            purpose=cabinet.purpose,
            latitude=cabinet.latitude,
            longitude=cabinet.longitude,
            tags=[TagOut.model_validate(t) for t in tags_map.get(cabinet_id, [])],
            created_at=cabinet.created_at,
            updated_at=cabinet.updated_at,
        )

    # Обновление ШУ
    async def update(self, cabinet_id: int, data: CabinetUpdateIn, actor_id: int, actor_role: str) -> CabinetOut:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")
        changed = data.model_dump(exclude_unset=True)
        if "type" in changed and changed["type"]:
            changed["type"] = await _resolve_type(self.session, changed["type"])
        for field, value in changed.items():
            setattr(cabinet, field, value)
        self.audit.log("cabinet.update", "cabinet", cabinet_id, actor_id, actor_role, {"fields": list(changed.keys())})
        await self.session.commit()
        await self.session.refresh(cabinet)
        return await self.get(cabinet_id)

    # Удаление ШУ (soft-delete: запись остаётся в БД, но перестаёт быть
    # доступна для поиска, привязки и повторного использования кода)
    async def delete(self, cabinet_id: int, actor_id: int, actor_role: str) -> None:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None or cabinet.deleted_at is not None:
            raise NotFoundError("ШУ не найден")
        self.audit.log("cabinet.delete", "cabinet", cabinet_id, actor_id, actor_role,
                       {"object_number": cabinet.object_number})
        await self.repo.soft_delete(cabinet)
        await self.session.commit()

    # Все ШУ
    async def list_all(
        self,
        query: str | None = None,
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        warranty_status: str | None = None,
        has_project: bool | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[CabinetListOut]:
        offset = (page - 1) * size
        cabinets, total = await self.repo.search(
            query=query, tag_ids=tag_ids,
            has_documents=has_documents, has_photos=has_photos,
            has_users=has_users, has_service_requests=has_service_requests,
            warranty_status=warranty_status, has_project=has_project,
            sort_by=sort_by, sort_order=sort_order,
            offset=offset, limit=size,
        )
        cabinet_ids = [c.id for c in cabinets]
        tags_map = await self.repo.get_tags(cabinet_ids)
        items = [
            CabinetListOut(
                id=c.id,
                unique_code=c.unique_code,
                type=c.type,
                object_number=c.object_number,
                purpose=c.purpose,
                warranty_starts_at=c.warranty_starts_at,
                warranty_ends_at=c.warranty_ends_at,
                warranty_status=_warranty_status(c.warranty_ends_at),
                admin_internal_name=c.admin_internal_name,
                admin_comment=c.admin_comment,
                tags=[TagOut.model_validate(t) for t in tags_map.get(c.id, [])],
                created_at=c.created_at,
            )
            for c in cabinets
        ]
        return make_page(items, total, page, size)

    # Привязать теги к ШУ
    async def set_tags(self, cabinet_id: int, tag_ids: list[int], actor_id: int, actor_role: str) -> None:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")
        await self.repo.set_tags(cabinet_id, tag_ids)
        self.audit.log("cabinet.set_tags", "cabinet", cabinet_id, actor_id, actor_role, {"tag_ids": tag_ids})
        await self.session.commit()

    async def get_geo(self) -> list[CabinetGeoItem]:
        rows = await self.repo.get_geo()
        return [
            CabinetGeoItem(
                id=row.id,
                object_number=row.object_number,
                admin_internal_name=row.admin_internal_name,
                warranty_status=_warranty_status(row.warranty_ends_at),
                latitude=row.latitude,
                longitude=row.longitude,
                has_open_requests=row.has_open_requests,
            )
            for row in rows
        ]

    # Генерация уникального кода(хранится в кур-коде)
    async def _generate_unique_code(self) -> str:
        while True:
            code = secrets.token_hex(8).upper()
            if await self.repo.find_by_code(code) is None:
                return code
