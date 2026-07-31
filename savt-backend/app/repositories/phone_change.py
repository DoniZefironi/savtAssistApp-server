from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_change_request import PhoneChangeRequest
from app.models.user import User
from app.utils.db import escape_like

_SORT_COLUMNS = {
    "created_at": PhoneChangeRequest.created_at,
    "resolved_at": PhoneChangeRequest.resolved_at,
    "status": PhoneChangeRequest.status,
    "user_full_name": User.full_name,
}


class PhoneChangeRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, user_id: int, new_phone: str, old_phone: str | None, user_comment: str | None
    ) -> PhoneChangeRequest:
        obj = PhoneChangeRequest(
            user_id=user_id,
            new_phone=new_phone,
            old_phone=old_phone,
            user_comment=user_comment,
            status="pending",
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def get_by_id(self, request_id: int) -> PhoneChangeRequest | None:
        return await self.session.get(PhoneChangeRequest, request_id)

    async def find_pending_for_user(self, user_id: int) -> PhoneChangeRequest | None:
        result = await self.session.execute(
            select(PhoneChangeRequest)
            .where(
                PhoneChangeRequest.user_id == user_id,
                PhoneChangeRequest.status == "pending",
            )
            .order_by(PhoneChangeRequest.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # Заявки на один и тот же номер от разных людей — не ошибка сама по себе
    # (номер мог реально перейти другому владельцу), но админ должен видеть,
    # что заявка не единственная, прежде чем одобрять
    async def find_pending_for_phone(self, new_phone: str) -> list[PhoneChangeRequest]:
        result = await self.session.execute(
            select(PhoneChangeRequest).where(
                PhoneChangeRequest.new_phone == new_phone,
                PhoneChangeRequest.status == "pending",
            )
        )
        return list(result.scalars().all())

    async def list_admin(
        self,
        status: str | None = None,
        resolved_by_admin_id: int | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[tuple], int]:
        stmt = select(PhoneChangeRequest, User).join(User, User.id == PhoneChangeRequest.user_id)

        if status:
            stmt = stmt.where(PhoneChangeRequest.status == status)
        if resolved_by_admin_id is not None:
            stmt = stmt.where(PhoneChangeRequest.resolved_by_admin_id == resolved_by_admin_id)
        if search:
            pattern = f"%{escape_like(search)}%"
            from sqlalchemy import or_
            stmt = stmt.where(or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
                PhoneChangeRequest.new_phone.ilike(pattern, escape="\\"),
                User.organization_name.ilike(pattern, escape="\\"),
            ))

        total = (await self.session.execute(
            select(func.count()).select_from(stmt.subquery())
        )).scalar() or 0

        column = _SORT_COLUMNS.get(sort_by, PhoneChangeRequest.created_at)
        stmt = stmt.order_by(column.asc() if sort_order == "asc" else column.desc())

        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return result.all(), total
