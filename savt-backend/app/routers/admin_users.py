from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.user import User
from app.schemas.admin_users import (
    AdminUserDetailOut,
    AdminUserListOut,
    BanUserIn,
    CabinetUserOut,
    RemoveUserFromCabinetIn,
)
from app.schemas.pagination import PageOut
from app.services.admin_user_service import AdminUserService

router = APIRouter(tags=["admin: users"])

# Все пользователи
@router.get("/admin/users", response_model=PageOut[AdminUserListOut])
async def list_users(
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = AdminUserService(session)
    return await service.list_users(query=search, is_active=is_active, page=page, size=size)

# Подробнее о пользователе
@router.get("/admin/users/{user_id}", response_model=AdminUserDetailOut)
async def get_user(
    user_id: int,
    admin: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = AdminUserService(session)
    return await service.get_user_detail(user_id)

# Забанить
@router.post("/admin/users/{user_id}/ban", status_code=status.HTTP_204_NO_CONTENT)
async def ban_user(
    user_id: int,
    payload: BanUserIn,
    admin: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    service = AdminUserService(session)
    await service.ban_user(user_id, payload.reason, admin.id)

# Разбанить
@router.post("/admin/users/{user_id}/unban", status_code=status.HTTP_204_NO_CONTENT)
async def unban_user(
    user_id: int,
    admin: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    service = AdminUserService(session)
    await service.unban_user(user_id, admin.id)

# Все пользователи подвязанные к шкафу
@router.get("/admin/cabinets/{cabinet_id}/users", response_model=list[CabinetUserOut])
async def list_cabinet_users(
    cabinet_id: int,
    admin: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    service = AdminUserService(session)
    return await service.list_cabinet_users(cabinet_id)

# Удалить пользователя со шкафа
@router.delete(
    "/admin/cabinets/{cabinet_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_user_from_cabinet(
    cabinet_id: int,
    user_id: int,
    payload: RemoveUserFromCabinetIn,
    admin: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    service = AdminUserService(session)
    await service.remove_user_from_cabinet(cabinet_id, user_id, payload.reason, admin.id)
