import asyncio
import logging
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
from app.services.audit_service import AuditLogger

_log = logging.getLogger(__name__)

_REQUEST_TYPE_LABELS = {
    "repair": "Ремонт",
    "maintenance": "Обслуживание",
    "inspection": "Осмотр",
    "other": "Другое",
}


def _to_out(req, cabinet) -> ServiceRequestOut:
    return ServiceRequestOut(
        id=req.id,
        user_id=req.user_id,
        cabinet_id=req.cabinet_id,
        cabinet_object_number=cabinet.object_number,
        request_type=req.request_type,
        description=req.description,
        status=req.status,
        bitrix_task_id=req.bitrix_task_id,
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
        bitrix_task_id=req.bitrix_task_id,
        created_at=req.created_at,
        closed_at=req.closed_at,
        user_full_name=user.full_name,
        user_phone=user.phone,
        user_type=user.user_type,
        organization_name=user.organization_name,
        user_is_verified=user.is_verified,
        user_registered_at=user.created_at,
    )


def _sync_to_bitrix(request_id: int, request_type: str, description: str, cabinet_object_number: str, cabinet_type: str, requester: str) -> None:
    async def _task():
        from app.database import AsyncSessionLocal
        from app.models.service_request import ServiceRequest
        from app.services import bitrix_service

        type_label = _REQUEST_TYPE_LABELS.get(request_type, request_type)
        title = f"{type_label}: ШУ {cabinet_object_number}"
        body = (
            f"Заявка №{request_id} из приложения SAVT\n"
            f"ШУ: {cabinet_object_number} ({cabinet_type})\n"
            f"Тип: {type_label}\n"
            f"От: {requester}\n\n"
            f"{description}"
        )
        try:
            task_id = await bitrix_service.create_task(title, body)
        except Exception:
            _log.exception("Bitrix task creation failed for service request %s", request_id)
            return
        if not task_id:
            return
        try:
            async with AsyncSessionLocal() as session:
                req = await session.get(ServiceRequest, request_id)
                if req is not None:
                    req.bitrix_task_id = task_id
                    await session.commit()
        except Exception:
            _log.exception("Failed to save bitrix_task_id for service request %s", request_id)

    asyncio.create_task(_task())


class ServiceRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ServiceRequestRepository(session)
        self.user_cabinet_repo = UserCabinetRepository(session)
        self.audit = AuditLogger(session)

    async def create(self, user_id: int, data: ServiceRequestCreateIn) -> ServiceRequestOut:
        link = await self.user_cabinet_repo.find(user_id, data.cabinet_id)
        if link is None:
            raise PermissionDeniedError("У вас нет доступа к этому ШУ")

        req = await self.repo.create(
            user_id=user_id,
            cabinet_id=data.cabinet_id,
            request_type=data.request_type,
            description=data.description,
        )
        await self.session.flush()
        self.audit.log("service_request.create", "service_request", req.id, user_id, "user",
                       {"cabinet_id": data.cabinet_id, "type": data.request_type})
        await self.session.commit()
        await self.session.refresh(req)

        from app.repositories.cabinet import CabinetRepository
        from app.repositories.user import UserRepository
        cabinet = await CabinetRepository(self.session).get_by_id(data.cabinet_id)
        user = await UserRepository(self.session).get_by_id(user_id)
        requester = user.full_name or user.phone if user else str(user_id)
        _sync_to_bitrix(req.id, req.request_type, req.description, cabinet.object_number, cabinet.type, requester)
        return _to_out(req, cabinet)

    async def list_for_user(
        self, user_id: int, status: str | None, page: int, size: int
    ) -> PageOut[ServiceRequestOut]:
        rows, total = await self.repo.list_for_user(
            user_id, status, offset=(page - 1) * size, limit=size
        )
        return make_page([_to_out(r, c) for r, c in rows], total, page, size)

    async def list_admin(
        self, status: str | None, cabinet_id: int | None, page: int, size: int,
        request_type: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> PageOut[ServiceRequestDetailOut]:
        rows, total = await self.repo.list_admin(
            status, cabinet_id, request_type=request_type, search=search, sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size
        )
        return make_page([_to_detail(r, u, c) for r, u, c in rows], total, page, size)

    async def update_status(
        self, req_id: int, data: ServiceRequestStatusIn, actor_id: int = 0, actor_role: str = "admin"
    ) -> ServiceRequestDetailOut:
        req = await self.repo.get_by_id(req_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")

        old_status = req.status
        req.status = data.status
        if data.status == "closed":
            req.closed_at = datetime.now(timezone.utc)
        else:
            req.closed_at = None

        self.audit.log("service_request.status_change", "service_request", req_id, actor_id, actor_role,
                       {"old_status": old_status, "new_status": data.status})
        await self.session.commit()
        await self.session.refresh(req)

        from app.repositories.cabinet import CabinetRepository
        from app.repositories.user import UserRepository
        cabinet = await CabinetRepository(self.session).get_by_id(req.cabinet_id)
        user = await UserRepository(self.session).get_by_id(req.user_id)
        return _to_detail(req, user, cabinet)
