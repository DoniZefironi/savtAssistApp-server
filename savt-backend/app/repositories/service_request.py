from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinets import Cabinet
from app.models.service_request import ServiceRequest
from app.models.user import User


class ServiceRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: int, cabinet_id: int, request_type: str, description: str) -> ServiceRequest:
        req = ServiceRequest(
            user_id=user_id,
            cabinet_id=cabinet_id,
            request_type=request_type,
            description=description,
        )
        self.session.add(req)
        await self.session.flush()
        return req

    async def get_by_id(self, req_id: int) -> ServiceRequest | None:
        return await self.session.get(ServiceRequest, req_id)

    async def list_for_user(
        self, user_id: int, status: str | None = None,
        offset: int = 0, limit: int = 20
    ) -> tuple[list[tuple], int]:
        conditions = [ServiceRequest.user_id == user_id]
        if status:
            conditions.append(ServiceRequest.status == status)

        count_stmt = select(func.count(ServiceRequest.id)).where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(ServiceRequest, Cabinet)
            .join(Cabinet, Cabinet.id == ServiceRequest.cabinet_id)
            .where(*conditions)
            .order_by(ServiceRequest.created_at.desc())
            .offset(offset).limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all(), total

    async def list_admin(
        self, status: str | None = None, cabinet_id: int | None = None,
        offset: int = 0, limit: int = 20
    ) -> tuple[list[tuple], int]:
        conditions = []
        if status:
            conditions.append(ServiceRequest.status == status)
        if cabinet_id:
            conditions.append(ServiceRequest.cabinet_id == cabinet_id)

        count_stmt = select(func.count(ServiceRequest.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(ServiceRequest, User, Cabinet)
            .join(User, User.id == ServiceRequest.user_id)
            .join(Cabinet, Cabinet.id == ServiceRequest.cabinet_id)
            .order_by(ServiceRequest.created_at.desc())
        )
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return result.all(), total
