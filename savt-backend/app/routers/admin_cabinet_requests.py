from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
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
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetRequestService(session)
    return await service.list_additions(status, page=page, size=size)

# Апрувнуть заявку
@router.post("/additions/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_addition(
    request_id: int,
    payload: ApproveAdditionIn,
    admin: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetRequestService(session)
    await service.approve_addition(request_id, payload, admin.id)

# Не апрувнуть заявку
@router.post("/additions/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_addition(
    request_id: int,
    payload: RejectRequestIn,
    admin: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetRequestService(session)
    await service.reject_addition(request_id, payload, admin.id)

# Все заявки по добавлению к уже подвязанному ШУ
@router.get("/shares", response_model=PageOut[ShareRequestOut])
async def list_shares(
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetRequestService(session)
    return await service.list_shares(status, page=page, size=size)

# Апрувнуть добавление
@router.post("/shares/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_share(
    request_id: int,
    payload: ApproveShareIn,
    admin: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetRequestService(session)
    await service.approve_share(request_id, payload, admin.id)

# Не правнуть добавление
@router.post("/shares/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_share(
    request_id: int,
    payload: RejectRequestIn,
    admin: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetRequestService(session)
    await service.reject_share(request_id, payload, admin.id)
