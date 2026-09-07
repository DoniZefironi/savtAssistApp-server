from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset_request import PasswordResetRequest
from app.models.user import User
from app.utils.db import escape_like

_SORT_COLUMNS = {
    "created_at": PasswordResetRequest.created_at,
    "resolved_at": PasswordResetRequest.resolved_at,
    "status": PasswordResetRequest.status,
    "user_full_name": User.full_name,
}


class PasswordResetRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, user_id: int, hashed_password: str, user_comment: str | None
    ) -> PasswordResetRequest:
        obj = PasswordResetRequest(
            user_id=user_id, hashed_password=hashed_password, user_comment=user_comment,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def get_by_id(self, request_id: int) -> PasswordResetRequest | None:
        return await self.session.get(PasswordResetRequest, request_id)

    async def find_pending_for_user(self, user_id: int) -> PasswordResetRequest | None:
        result = await self.session.execute(
            select(PasswordResetRequest).where(
                PasswordResetRequest.user_id == user_id,
                PasswordResetRequest.status == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def list_admin(
        self,
        status: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[tuple], int]:
        stmt = select(PasswordResetRequest, User).join(User, User.id == PasswordResetRequest.user_id)

        if status:
            stmt = stmt.where(PasswordResetRequest.status == status)
        if search:
            pattern = f"%{escape_like(search)}%"
            stmt = stmt.where(or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
                User.organization_name.ilike(pattern, escape="\\"),
            ))

        total = (await self.session.execute(
            select(func.count()).select_from(stmt.subquery())
        )).scalar() or 0

        column = _SORT_COLUMNS.get(sort_by, PasswordResetRequest.created_at)
        stmt = stmt.order_by(column.asc() if sort_order == "asc" else column.desc())

        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return result.all(), total
