from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.user import User
from app.schemas.cabinet import CabinetCreateIn, CabinetListOut, CabinetOut, CabinetUpdateIn
from app.services.cabinet_service import CabinetService

router = APIRouter(prefix="/admin/cabinets", tags=["admin: cabinets"])

# Создать ШУ
@router.post("", response_model=CabinetOut, status_code=status.HTTP_201_CREATED)
async def create_cabinet(
    payload: CabinetCreateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetService(session)
    return await service.create(payload)

# Все ШУ
@router.get("", response_model=list[CabinetListOut])
async def list_cabinets(
    search: str | None = Query(None),
    sort_by: str = Query("created_at", pattern="^(type|warranty_ends_at|object_number|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetService(session)
    return await service.list_all(query=search, sort_by=sort_by, sort_order=sort_order)

# Подробнее о ШУ
@router.get("/{cabinet_id}", response_model=CabinetOut)
async def get_cabinet(
    cabinet_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetService(session)
    return await service.get(cabinet_id)

# Обновить инфу о ШУ
@router.patch("/{cabinet_id}", response_model=CabinetOut)
async def update_cabinet(
    cabinet_id: int,
    payload: CabinetUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetService(session)
    return await service.update(cabinet_id, payload)

# Удалить ШУ
@router.delete("/{cabinet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cabinet(
    cabinet_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    service = CabinetService(session)
    await service.delete(cabinet_id)
