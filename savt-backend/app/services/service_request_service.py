from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.repositories.service_request import ServiceRequestRepository
from app.repositories.cabinet import UserCabinetRepository
from app.schemas.pagination import PageOut, make_page
from app.schemas.service_requests import (
    ServiceRequestCreateIn,
    ServiceRequestDetailOut,
    ServiceRequestOut,
    ServiceRequestStatusIn,
)


def _to_out(req, cabinet) -> ServiceRequestOut:
    return ServiceRequestOut(
        id=req.id,
        user_id=req.user_id,
        cabinet_id=req.cabinet_id,
        cabinet_object_number=cabinet.object_number,
        request_type=req.request_type,
        description=req.description,
        status=req.status,
        created_at=req.created_at,
        closed_at=req.closed_at,
    )


def _to_detail(req, user, cabinet) -> ServiceRequestDetailOut:
    return ServiceRequestDetailOut(
        id=req.id,
        user_id=req.user_id,
        cabinet_id=req.cabinet_id,
        cabinet_object_number=cabinet.object_number,
        request_type=req.request_type,
        description=req.description,
        status=req.status,
        created_at=req.created_at,
        closed_at=req.closed_at,
        user_full_name=user.full_name,
        user_phone=user.phone,
    )


class ServiceRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ServiceRequestRepository(session)
        self.user_cabinet_repo = UserCabinetRepository(session)

    async def create(self, user_id: int, data: ServiceRequestCreateIn) -> ServiceRequestOut:
        # Пользователь должен быть привязан к этому ШУ
        link = await self.user_cabinet_repo.find(user_id, data.cabinet_id)
        if link is None:
            raise PermissionDeniedError("У вас нет доступа к этому ШУ")

        req = await self.repo.create(
            user_id=user_id,
            cabinet_id=data.cabinet_id,
            request_type=data.request_type,
            description=data.description,
        )
        await self.session.commit()
        await self.session.refresh(req)

        from app.repositories.cabinet import CabinetRepository
        cabinet = await CabinetRepository(self.session).get_by_id(data.cabinet_id)
        return _to_out(req, cabinet)

    async def list_for_user(
        self, user_id: int, status: str | None, page: int, size: int
    ) -> PageOut[ServiceRequestOut]:
        rows, total = await self.repo.list_for_user(
            user_id, status, offset=(page - 1) * size, limit=size
        )
        return make_page([_to_out(r, c) for r, c in rows], total, page, size)

    async def list_admin(
        self, status: str | None, cabinet_id: int | None, page: int, size: int
    ) -> PageOut[ServiceRequestDetailOut]:
        rows, total = await self.repo.list_admin(
            status, cabinet_id, offset=(page - 1) * size, limit=size
        )
        return make_page([_to_detail(r, u, c) for r, u, c in rows], total, page, size)

    async def update_status(
        self, req_id: int, data: ServiceRequestStatusIn
    ) -> ServiceRequestDetailOut:
        req = await self.repo.get_by_id(req_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")

        req.status = data.status
        if data.status == "closed":
            req.closed_at = datetime.now(timezone.utc)
        else:
            req.closed_at = None

        await self.session.commit()
        await self.session.refresh(req)

        from app.repositories.cabinet import CabinetRepository
        from app.repositories.user import UserRepository
        cabinet = await CabinetRepository(self.session).get_by_id(req.cabinet_id)
        user = await UserRepository(self.session).get_by_id(req.user_id)
        return _to_detail(req, user, cabinet)
