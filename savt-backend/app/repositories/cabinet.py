from datetime import datetime, timezone

from sqlalchemy import delete, exists, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.cabinet_share_request import CabinetShareRequest
from app.models.cabinet_tag import CabinetTag
from app.models.cabinets import Cabinet
from app.models.cabinet_photo import CabinetPhoto
from app.models.document import Document
from app.models.service_request import ServiceRequest
from app.models.tag import Tag
from app.utils.db import escape_like
from app.models.user import User
from app.models.user_cabinet import UserCabinet
from app.repositories.base import BaseRepository


class CabinetRepository(BaseRepository[Cabinet]):
    def __init__(self, session: AsyncSession):
        super().__init__(Cabinet, session)
    # поиск кода
    async def find_by_code(self, unique_code: str) -> Cabinet | None:
        result = await self.session.execute(
            select(Cabinet).where(Cabinet.unique_code == unique_code)
        )
        return result.scalar_one_or_none()
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
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Cabinet], int]:
        conditions = []
        if query:
            pattern = f"%{escape_like(query)}%"
            conditions.append(or_(
                Cabinet.type.ilike(pattern, escape="\\"),
                Cabinet.object_number.ilike(pattern, escape="\\"),
                Cabinet.admin_internal_name.ilike(pattern, escape="\\"),
                Cabinet.purpose.ilike(pattern, escape="\\"),
                Cabinet.description.ilike(pattern, escape="\\"),
                Cabinet.admin_comment.ilike(pattern, escape="\\"),
            ))
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
            user_exists = exists(
                select(UserCabinet.id).where(UserCabinet.cabinet_id == Cabinet.id)
            )
            conditions.append(user_exists if has_users else ~user_exists)

        if has_service_requests is not None:
            sr_exists = exists(
                select(ServiceRequest.id).where(ServiceRequest.cabinet_id == Cabinet.id)
            )
            conditions.append(sr_exists if has_service_requests else ~sr_exists)

        if warranty_status == "active":
            conditions.append(Cabinet.warranty_ends_at.isnot(None))
            conditions.append(Cabinet.warranty_ends_at >= datetime.now(timezone.utc))
        elif warranty_status == "expired":
            conditions.append(Cabinet.warranty_ends_at.isnot(None))
            conditions.append(Cabinet.warranty_ends_at < datetime.now(timezone.utc))
        elif warranty_status == "none":
            conditions.append(Cabinet.warranty_ends_at.is_(None))

        count_stmt = select(func.count(Cabinet.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        sort_column = {
            "type": Cabinet.type,
            "warranty_ends_at": Cabinet.warranty_ends_at,
            "object_number": Cabinet.object_number,
            "admin_internal_name": Cabinet.admin_internal_name,
            "created_at": Cabinet.created_at,
        }.get(sort_by, Cabinet.created_at)

        stmt = select(Cabinet)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(sort_column.asc() if sort_order == "asc" else sort_column.desc())
        stmt = stmt.offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

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


class UserCabinetRepository(BaseRepository[UserCabinet]):
    def __init__(self, session: AsyncSession):
        super().__init__(UserCabinet, session)

    async def find(self, user_id: int, cabinet_id: int) -> UserCabinet | None:
        result = await self.session.execute(
            select(UserCabinet).where(
                UserCabinet.user_id == user_id,
                UserCabinet.cabinet_id == cabinet_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list:
        result = await self.session.execute(
            select(UserCabinet, Cabinet)
            .join(Cabinet, Cabinet.id == UserCabinet.cabinet_id)
            .where(UserCabinet.user_id == user_id)
            .order_by(UserCabinet.is_primary.desc(), UserCabinet.added_at.desc())
        )
        return result.all()

    async def get_with_cabinet(self, user_id: int, cabinet_id: int):
        result = await self.session.execute(
            select(UserCabinet, Cabinet)
            .join(Cabinet, Cabinet.id == UserCabinet.cabinet_id)
            .where(
                UserCabinet.user_id == user_id,
                UserCabinet.cabinet_id == cabinet_id,
            )
        )
        return result.one_or_none()

    async def list_cabinet_users(self, cabinet_id: int) -> list:
        result = await self.session.execute(
            select(UserCabinet, User)
            .join(User, User.id == UserCabinet.user_id)
            .where(UserCabinet.cabinet_id == cabinet_id)
            .order_by(UserCabinet.is_primary.desc(), UserCabinet.added_at)
        )
        return result.all()

    async def has_primary(self, cabinet_id: int) -> bool:
        result = await self.session.execute(
            select(UserCabinet).where(
                UserCabinet.cabinet_id == cabinet_id,
                UserCabinet.is_primary == True,
            )
        )
        return result.scalar_one_or_none() is not None


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

    async def find_pending_share(self, user_id: int, cabinet_id: int) -> CabinetShareRequest | None:
        result = await self.session.execute(
            select(CabinetShareRequest).where(
                CabinetShareRequest.user_id == user_id,
                CabinetShareRequest.cabinet_id == cabinet_id,
                CabinetShareRequest.status == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def create_share(
        self, user_id: int, cabinet_id: int, user_comment: str | None = None
    ) -> CabinetShareRequest:
        obj = CabinetShareRequest(
            user_id=user_id,
            cabinet_id=cabinet_id,
            user_comment=user_comment,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def create_addition(
        self, user_id: int, photo_url: str, user_comment: str | None = None
    ) -> CabinetAdditionRequest:
        obj = CabinetAdditionRequest(
            user_id=user_id,
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

    async def get_share(self, request_id: int) -> CabinetShareRequest | None:
        result = await self.session.execute(
            select(CabinetShareRequest).where(CabinetShareRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    async def list_additions(
        self,
        status: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list, int]:
        conditions = []
        if status:
            conditions.append(CabinetAdditionRequest.status == status)
        if search:
            pattern = f"%{escape_like(search)}%"
            conditions.append(or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
                User.organization_name.ilike(pattern, escape="\\"),
            ))

        count_stmt = select(func.count(CabinetAdditionRequest.id)).join(User, User.id == CabinetAdditionRequest.user_id)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        _sort_col = {
            "created_at": CabinetAdditionRequest.created_at,
            "status": CabinetAdditionRequest.status,
            "user_full_name": User.full_name,
        }.get(sort_by, CabinetAdditionRequest.created_at)
        order = _sort_col.asc() if sort_order == "asc" else _sort_col.desc()

        stmt = select(CabinetAdditionRequest, User).join(User, User.id == CabinetAdditionRequest.user_id)
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.order_by(order).offset(offset).limit(limit))
        return result.all(), total

    async def list_shares(
        self,
        status: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list, int]:
        conditions = []
        if status:
            conditions.append(CabinetShareRequest.status == status)
        if search:
            pattern = f"%{escape_like(search)}%"
            conditions.append(or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
                User.organization_name.ilike(pattern, escape="\\"),
                Cabinet.type.ilike(pattern, escape="\\"),
                Cabinet.object_number.ilike(pattern, escape="\\"),
                Cabinet.admin_internal_name.ilike(pattern, escape="\\"),
            ))

        count_stmt = (
            select(func.count(CabinetShareRequest.id))
            .join(User, User.id == CabinetShareRequest.user_id)
            .join(Cabinet, Cabinet.id == CabinetShareRequest.cabinet_id)
        )
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        _sort_col = {
            "created_at": CabinetShareRequest.created_at,
            "status": CabinetShareRequest.status,
            "user_full_name": User.full_name,
            "cabinet_object_number": Cabinet.object_number,
        }.get(sort_by, CabinetShareRequest.created_at)
        order = _sort_col.asc() if sort_order == "asc" else _sort_col.desc()

        stmt = (
            select(CabinetShareRequest, User, Cabinet)
            .join(User, User.id == CabinetShareRequest.user_id)
            .join(Cabinet, Cabinet.id == CabinetShareRequest.cabinet_id)
        )
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.order_by(order).offset(offset).limit(limit))
        return result.all(), total
