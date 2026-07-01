from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.pagination import PageOut
from app.schemas.requests import (
    AdditionRequestOut,
    ApproveAdditionIn,
    ApproveShareIn,
    RejectRequestIn,
    ShareRequestOut,
)
from app.services.cabinet_request_service import CabinetRequestService

router = APIRouter(prefix="/admin/cabinet-requests", tags=["admin: cabinet requests"])

# Список заявок по добавлению по фото
@router.get("/additions", response_model=PageOut[AdditionRequestOut])
async def list_additions(
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    resolved_by_admin_id: int | None = Query(None, gt=0),
    search: str | None = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("created_at", pattern="^(created_at|resolved_at|status|user_full_name)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetRequestService(session).list_additions(
        status=status, resolved_by_admin_id=resolved_by_admin_id, search=search,
        sort_by=sort_by, sort_order=sort_order, page=page, size=size,
    )

# Апрувнуть заявку
@router.post("/additions/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_addition(
    request_id: int,
    payload: ApproveAdditionIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await CabinetRequestService(session).approve_addition(request_id, payload, actor.id, actor_role)

# Не апрувнуть заявку
@router.post("/additions/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_addition(
    request_id: int,
    payload: RejectRequestIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await CabinetRequestService(session).reject_addition(request_id, payload, actor.id, actor_role)

# Все заявки по добавлению к уже подвязанному ШУ
@router.get("/shares", response_model=PageOut[ShareRequestOut])
async def list_shares(
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    resolved_by_admin_id: int | None = Query(None, gt=0),
    search: str | None = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("created_at", pattern="^(created_at|resolved_at|status|user_full_name|cabinet_object_number)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetRequestService(session).list_shares(
        status=status, resolved_by_admin_id=resolved_by_admin_id, search=search,
        sort_by=sort_by, sort_order=sort_order, page=page, size=size,
    )

# Апрувнуть добавление
@router.post("/shares/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_share(
    request_id: int,
    payload: ApproveShareIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await CabinetRequestService(session).approve_share(request_id, payload, actor.id, actor_role)

# Отклонить добавление
@router.post("/shares/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_share(
    request_id: int,
    payload: RejectRequestIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await CabinetRequestService(session).reject_share(request_id, payload, actor.id, actor_role)
