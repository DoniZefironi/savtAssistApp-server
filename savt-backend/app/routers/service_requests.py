from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_current_user, get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.pagination import PageOut
from app.schemas.service_requests import (
    ServiceRequestCreateIn,
    ServiceRequestDetailOut,
    ServiceRequestOut,
    ServiceRequestStatusIn,
)
from app.services.service_request_service import ServiceRequestService

router = APIRouter(tags=["service requests"])

_STATUS_PATTERN = "^(open|in_progress|closed)$"


# --- Пользователь ---

@router.post("/service-requests", response_model=ServiceRequestOut, status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: ServiceRequestCreateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ServiceRequestService(session).create(current_user.id, payload)


@router.get("/service-requests", response_model=PageOut[ServiceRequestOut])
async def list_my_requests(
    status: str | None = Query(None, pattern=_STATUS_PATTERN),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ServiceRequestService(session).list_for_user(
        current_user.id, status, page, size
    )


# --- Администратор / Оператор ---

@router.get("/admin/service-requests", response_model=PageOut[ServiceRequestDetailOut])
async def list_all_requests(
    status: str | None = Query(None, pattern=_STATUS_PATTERN),
    cabinet_id: int | None = Query(None, gt=0),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await ServiceRequestService(session).list_admin(
        status, cabinet_id, page, size
    )


@router.patch(
    "/admin/service-requests/{req_id}/status",
    response_model=ServiceRequestDetailOut,
)
async def update_status(
    req_id: int,
    payload: ServiceRequestStatusIn,
    actor: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await ServiceRequestService(session).update_status(req_id, payload, actor.id, actor_role)
