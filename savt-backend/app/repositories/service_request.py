from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinets import Cabinet
from app.models.service_request import ServiceRequest
from app.models.user import User
from app.utils.db import escape_like


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
        request_type: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0, limit: int = 20
    ) -> tuple[list[tuple], int]:
        conditions = []
        if status:
            conditions.append(ServiceRequest.status == status)
        if cabinet_id:
            conditions.append(ServiceRequest.cabinet_id == cabinet_id)
        if request_type:
            conditions.append(ServiceRequest.request_type == request_type)
        if search:
            pattern = f"%{escape_like(search)}%"
            conditions.append(or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
                User.organization_name.ilike(pattern, escape="\\"),
                Cabinet.object_number.ilike(pattern, escape="\\"),
                Cabinet.admin_internal_name.ilike(pattern, escape="\\"),
                ServiceRequest.request_type.ilike(pattern, escape="\\"),
                ServiceRequest.description.ilike(pattern, escape="\\"),
            ))

        count_stmt = (
            select(func.count(ServiceRequest.id))
            .join(User, User.id == ServiceRequest.user_id)
            .join(Cabinet, Cabinet.id == ServiceRequest.cabinet_id)
        )
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        _sort_col = {
            "created_at": ServiceRequest.created_at,
            "closed_at": ServiceRequest.closed_at,
            "status": ServiceRequest.status,
            "user_full_name": User.full_name,
            "cabinet_object_number": Cabinet.object_number,
            "request_type": ServiceRequest.request_type,
        }.get(sort_by, ServiceRequest.created_at)
        order = _sort_col.asc() if sort_order == "asc" else _sort_col.desc()

        stmt = (
            select(ServiceRequest, User, Cabinet)
            .join(User, User.id == ServiceRequest.user_id)
            .join(Cabinet, Cabinet.id == ServiceRequest.cabinet_id)
        )
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.order_by(order).offset(offset).limit(limit))
        return result.all(), total
