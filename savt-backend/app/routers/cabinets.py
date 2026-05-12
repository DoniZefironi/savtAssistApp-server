from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.cabinet import (
    AddByPhotoIn,
    AddByPhotoOut,
    AddByQrIn,
    AddByQrOut,
    UserCabinetDetailOut,
    UserCabinetListItemOut,
    UserCabinetPatchIn,
)
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

# Удаление ШУ
@router.delete("/{cabinet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_cabinet(
    cabinet_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserCabinetService(session)
    await service.remove_cabinet(current_user.id, cabinet_id)

# Добавить ШУ по кур-коду
@router.post("/add-by-qr", response_model=AddByQrOut)
async def add_by_qr(
    payload: AddByQrIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserCabinetService(session)
    result = await service.add_by_qr(user_id=current_user.id, unique_code=payload.unique_code)
    return AddByQrOut(**result)

# Добавить ШУ по фото(пользователь)
@router.post("/add-by-photo", response_model=AddByPhotoOut, status_code=status.HTTP_201_CREATED)
async def add_by_photo(
    payload: AddByPhotoIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserCabinetService(session)
    request_id = await service.add_by_photo(
        user_id=current_user.id,
        photo_url=payload.photo_url,
        user_comment=payload.user_comment,
    )
    return AddByPhotoOut(request_id=request_id)
