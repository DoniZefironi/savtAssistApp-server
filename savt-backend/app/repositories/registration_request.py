from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registration_request import RegistrationRequest
from app.utils.db import fuzzy_condition


class RegistrationRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> RegistrationRequest:
        obj = RegistrationRequest(**kwargs)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def get(self, request_id: int) -> RegistrationRequest | None:
        return await self.session.get(RegistrationRequest, request_id)

    # Не даём копиться нескольким pending-заявкам на один и тот же номер —
    # админ разбирает их по одной, вторая заявка на тот же номер только запутает
    async def has_pending_for_phone(self, phone: str) -> bool:
        result = await self.session.execute(
            select(RegistrationRequest.id).where(
                RegistrationRequest.phone == phone,
                RegistrationRequest.status == "pending",
            )
        )
        return result.scalar_one_or_none() is not None

    async def list_requests(
        self,
        status: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[RegistrationRequest], int]:
        conditions = []
        if status:
            conditions.append(RegistrationRequest.status == status)
        if search:
            conditions.append(fuzzy_condition(
                search,
                RegistrationRequest.full_name, RegistrationRequest.phone,
                RegistrationRequest.organization_name,
            ))

        count_stmt = select(func.count(RegistrationRequest.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        sort_col = {
            "created_at": RegistrationRequest.created_at,
            "resolved_at": RegistrationRequest.resolved_at,
            "status": RegistrationRequest.status,
            "full_name": RegistrationRequest.full_name,
        }.get(sort_by, RegistrationRequest.created_at)
        order = sort_col.asc() if sort_order == "asc" else sort_col.desc()

        stmt = select(RegistrationRequest)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(order).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total
