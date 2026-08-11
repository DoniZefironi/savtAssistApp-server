from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.pagination import PageOut
from app.schemas.requests import (
    AdditionRequestOut,
    ApproveAdditionIn,
    RejectRequestIn,
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

# Заявок на доступ к отдельному ШУ больше нет — доступ выводится из проекта,
# см. GET/POST /admin/project-requests (заявка на вступление в проект целиком)
