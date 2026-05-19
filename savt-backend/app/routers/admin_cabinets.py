from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.cabinet import CabinetCreateIn, CabinetListOut, CabinetOut, CabinetUpdateIn
from app.schemas.pagination import PageOut
from app.services.cabinet_service import CabinetService

router = APIRouter(prefix="/admin/cabinets", tags=["admin: cabinets"])

# Создать ШУ
@router.post("", response_model=CabinetOut, status_code=status.HTTP_201_CREATED)
async def create_cabinet(
    payload: CabinetCreateIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).create(payload, actor.id, actor_role)

# Все ШУ
@router.get("", response_model=PageOut[CabinetListOut])
async def list_cabinets(
    search: str | None = Query(None),
    sort_by: str = Query("created_at", pattern="^(type|warranty_ends_at|object_number|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).list_all(query=search, sort_by=sort_by, sort_order=sort_order, page=page, size=size)

# Подробнее о ШУ
@router.get("/{cabinet_id}", response_model=CabinetOut)
async def get_cabinet(
    cabinet_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).get(cabinet_id)

# Обновить инфу о ШУ
@router.patch("/{cabinet_id}", response_model=CabinetOut)
async def update_cabinet(
    cabinet_id: int,
    payload: CabinetUpdateIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).update(cabinet_id, payload, actor.id, actor_role)

# Удалить ШУ
@router.delete("/{cabinet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cabinet(
    cabinet_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await CabinetService(session).delete(cabinet_id, actor.id, actor_role)
