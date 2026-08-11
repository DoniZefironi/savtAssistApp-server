from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinets import Cabinet
from app.models.project import Project
from app.models.service_request import ServiceRequest
from app.models.user import User
from app.utils.db import fuzzy_condition


class ServiceRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, user_id: int, request_type: str, description: str,
        cabinet_id: int | None = None, project_id: int | None = None,
        client_token: str | None = None,
    ) -> ServiceRequest:
        req = ServiceRequest(
            user_id=user_id,
            cabinet_id=cabinet_id,
            project_id=project_id,
            request_type=request_type,
            description=description,
            client_token=client_token,
        )
        self.session.add(req)
        await self.session.flush()
        return req

    async def get_by_id(self, req_id: int) -> ServiceRequest | None:
        return await self.session.get(ServiceRequest, req_id)

    async def find_by_client_token(self, user_id: int, client_token: str) -> ServiceRequest | None:
        result = await self.session.execute(
            select(ServiceRequest).where(
                ServiceRequest.user_id == user_id,
                ServiceRequest.client_token == client_token,
            )
        )
        return result.scalar_one_or_none()

    async def find_by_bitrix_task_id(self, bitrix_task_id: str) -> ServiceRequest | None:
        result = await self.session.execute(
            select(ServiceRequest).where(ServiceRequest.bitrix_task_id == bitrix_task_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: int, status: str | None = None,
        offset: int = 0, limit: int = 20
    ) -> tuple[list[tuple], int]:
        conditions = [ServiceRequest.user_id == user_id]
        if status:
            conditions.append(ServiceRequest.status == status)

        count_stmt = select(func.count(ServiceRequest.id)).where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # outerjoin — cabinet_id/project_id взаимоисключающие, у заявки по
        # проекту нет ШУ и наоборот (см. CHECK на модели)
        stmt = (
            select(ServiceRequest, Cabinet, Project)
            .outerjoin(Cabinet, Cabinet.id == ServiceRequest.cabinet_id)
            .outerjoin(Project, Project.id == ServiceRequest.project_id)
            .where(*conditions)
            .order_by(ServiceRequest.created_at.desc())
            .offset(offset).limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all(), total

    async def list_admin(
        self, status: str | None = None, cabinet_id: int | None = None,
        project_id: int | None = None,
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
        if project_id:
            conditions.append(ServiceRequest.project_id == project_id)
        if request_type:
            conditions.append(ServiceRequest.request_type == request_type)
        if search:
            conditions.append(fuzzy_condition(
                search,
                User.full_name, User.phone, User.organization_name,
                Cabinet.object_number, Cabinet.admin_internal_name, Project.name,
                ServiceRequest.request_type, ServiceRequest.description,
            ))

        count_stmt = (
            select(func.count(ServiceRequest.id))
            .join(User, User.id == ServiceRequest.user_id)
            .outerjoin(Cabinet, Cabinet.id == ServiceRequest.cabinet_id)
            .outerjoin(Project, Project.id == ServiceRequest.project_id)
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
            select(ServiceRequest, User, Cabinet, Project)
            .join(User, User.id == ServiceRequest.user_id)
            .outerjoin(Cabinet, Cabinet.id == ServiceRequest.cabinet_id)
            .outerjoin(Project, Project.id == ServiceRequest.project_id)
        )
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.order_by(order).offset(offset).limit(limit))
        return result.all(), total
