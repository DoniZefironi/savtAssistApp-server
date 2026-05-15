from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.cabinet_share_request import CabinetShareRequest
from app.models.cabinets import Cabinet
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
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Cabinet], int]:
        conditions = []
        if query:
            conditions.append(or_(
                Cabinet.type.ilike(f"%{query}%"),
                Cabinet.object_number.ilike(f"%{query}%"),
                Cabinet.admin_internal_name.ilike(f"%{query}%"),
            ))

        count_stmt = select(func.count(Cabinet.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        sort_column = {
            "type": Cabinet.type,
            "warranty_ends_at": Cabinet.warranty_ends_at,
            "object_number": Cabinet.object_number,
            "created_at": Cabinet.created_at,
        }.get(sort_by, Cabinet.created_at)

        stmt = select(Cabinet)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(sort_column.asc() if sort_order == "asc" else sort_column.desc())
        stmt = stmt.offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total


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
        self, status: str | None = None, offset: int = 0, limit: int = 20
    ) -> tuple[list, int]:
        count_stmt = select(func.count(CabinetAdditionRequest.id))
        if status:
            count_stmt = count_stmt.where(CabinetAdditionRequest.status == status)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(CabinetAdditionRequest, User)
            .join(User, User.id == CabinetAdditionRequest.user_id)
            .order_by(CabinetAdditionRequest.created_at.desc())
        )
        if status:
            stmt = stmt.where(CabinetAdditionRequest.status == status)
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return result.all(), total

    async def list_shares(
        self, status: str | None = None, offset: int = 0, limit: int = 20
    ) -> tuple[list, int]:
        count_stmt = select(func.count(CabinetShareRequest.id))
        if status:
            count_stmt = count_stmt.where(CabinetShareRequest.status == status)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(CabinetShareRequest, User, Cabinet)
            .join(User, User.id == CabinetShareRequest.user_id)
            .join(Cabinet, Cabinet.id == CabinetShareRequest.cabinet_id)
            .order_by(CabinetShareRequest.created_at.desc())
        )
        if status:
            stmt = stmt.where(CabinetShareRequest.status == status)
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return result.all(), total
