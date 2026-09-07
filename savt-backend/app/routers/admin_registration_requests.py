from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.auth import AdminRegistrationRequestOut, ApproveRegistrationRequestIn
from app.schemas.pagination import PageOut
from app.schemas.requests import RejectRequestIn
from app.services.registration_request_service import RegistrationRequestService

router = APIRouter(prefix="/admin/registration-requests", tags=["admin: registration requests"])


# Все заявки на регистрацию
@router.get("", response_model=PageOut[AdminRegistrationRequestOut])
async def list_requests(
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    search: str | None = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("created_at", pattern="^(created_at|resolved_at|status|full_name)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await RegistrationRequestService(session).list_requests(
        status=status, search=search, sort_by=sort_by, sort_order=sort_order, page=page, size=size,
    )


# Одобрить — заводит аккаунт из данных заявки
@router.post("/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_request(
    request_id: int,
    payload: ApproveRegistrationRequestIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await RegistrationRequestService(session).approve(request_id, payload, actor.id, actor_role)


# Отклонить
@router.post("/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_request(
    request_id: int,
    payload: RejectRequestIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await RegistrationRequestService(session).reject(request_id, payload, actor.id, actor_role)
