from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.auth import AdminPhoneChangeRequestOut
from app.schemas.pagination import PageOut
from app.schemas.requests import ApproveShareIn, RejectRequestIn
from app.services.phone_change_service import PhoneChangeService

router = APIRouter(prefix="/admin/phone-change-requests", tags=["admin: phone change requests"])


# Список заявок на смену номера. Смотреть может и оператор, одобрять — только админ
# (та же раскладка прав, что у заявок на ШУ и на проекты)
@router.get("", response_model=PageOut[AdminPhoneChangeRequestOut])
async def list_requests(
    status: str | None = Query(None, pattern="^(pending|approved|rejected|cancelled)$"),
    resolved_by_admin_id: int | None = Query(None, gt=0),
    search: str | None = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("created_at", pattern="^(created_at|resolved_at|status|user_full_name)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await PhoneChangeService(session).list_requests(
        status=status, resolved_by_admin_id=resolved_by_admin_id, search=search,
        sort_by=sort_by, sort_order=sort_order, page=page, size=size,
    )


# Одобрить — номер меняется сразу. Администратор перед этим обязан
# самостоятельно убедиться, что номер принадлежит заявителю: система
# подтвердить это не может, SMS отключены (см. PhoneChangeService)
@router.post("/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_request(
    request_id: int,
    payload: ApproveShareIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await PhoneChangeService(session).approve(request_id, payload.admin_response, actor.id, actor_role)


@router.post("/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_request(
    request_id: int,
    payload: RejectRequestIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await PhoneChangeService(session).reject(request_id, payload, actor.id, actor_role)
