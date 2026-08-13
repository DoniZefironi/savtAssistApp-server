from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.cabinet import (
    AddByPhotoIn,
    AddByPhotoOut,
    UserCabinetDetailOut,
    UserCabinetListItemOut,
    UserCabinetPatchIn,
)
from app.schemas.pagination import PageOut
from app.schemas.telemetry import TelemetryEventOut
from app.services.telemetry_service import UserTelemetryService
from app.services.user_cabinet_service import UserCabinetService

router = APIRouter(prefix="/cabinets", tags=["cabinets"])

# Все ШУ
@router.get("", response_model=list[UserCabinetListItemOut])
async def list_cabinets(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserCabinetService(session)
    return await service.list_cabinets(current_user.id)

# Подробнее об ШУ
@router.get("/{cabinet_id}", response_model=UserCabinetDetailOut)
async def get_cabinet(
    cabinet_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserCabinetService(session)
    return await service.get_cabinet(current_user.id, cabinet_id)

# Обновить инфу по ШУ(название, комментарий)
@router.patch("/{cabinet_id}", response_model=UserCabinetDetailOut)
async def update_cabinet(
    cabinet_id: int,
    payload: UserCabinetPatchIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserCabinetService(session)
    return await service.update_cabinet(current_user.id, cabinet_id, payload)

# Отвязки одного ШУ больше нет — доступ выводится из проекта целиком, см.
# DELETE /projects/{project_id} (выйти из проекта — теряет доступ разом ко
# всем его шкафам)

# Лента событий с MQTT-контроллера ШУ (аварии/сообщения) — регистры уже
# расшифрованы по карте (стандартная + добавки этого ШУ, см. AdminRegisterMapService)
@router.get("/{cabinet_id}/telemetry", response_model=PageOut[TelemetryEventOut])
async def get_cabinet_telemetry(
    cabinet_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserTelemetryService(session)
    return await service.list_for_cabinet(current_user.id, cabinet_id, page, size)

# Добавить ШУ по фото(пользователь) — "в моём проекте не хватает шкафа"
@router.post("/add-by-photo", response_model=AddByPhotoOut, status_code=status.HTTP_201_CREATED)
async def add_by_photo(
    payload: AddByPhotoIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserCabinetService(session)
    request_id = await service.add_by_photo(
        user_id=current_user.id,
        project_id=payload.project_id,
        photo_url=payload.photo_url,
        user_comment=payload.user_comment,
    )
    return AddByPhotoOut(request_id=request_id)
