from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.admin_users import (
    AdminUserDetailOut,
    AdminUserListOut,
    BanUserIn,
    CabinetUserOut,
    CreateAdminIn,
    CreateOperatorIn,
    RemoveUserFromCabinetIn,
)
from app.schemas.pagination import PageOut
from app.services.admin_user_service import AdminUserService

router = APIRouter(tags=["admin: users"])

# Создать администратора (только суперадмин)
@router.post("/admin/users/admins", response_model=AdminUserListOut, status_code=status.HTTP_201_CREATED)
async def create_admin(
    payload: CreateAdminIn,
    actor: User = Depends(require_role(RoleName.SUPERADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await AdminUserService(session).create_admin(payload, actor.id, actor_role)


# Создать оператора
@router.post("/admin/users/operators", response_model=AdminUserListOut, status_code=status.HTTP_201_CREATED)
async def create_operator(
    payload: CreateOperatorIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await AdminUserService(session).create_operator(payload, actor.id, actor_role)


# Пользователи (role=user)
@router.get("/admin/users", response_model=PageOut[AdminUserListOut])
async def list_users(
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    sort_by: str = Query("created_at", pattern="^(created_at|full_name|phone|email|role)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminUserService(session).list_users(
        query=search, is_active=is_active, role="user",
        sort_by=sort_by, sort_order=sort_order,
        page=page, size=size,
    )


# Операторы (role=operator)
@router.get("/admin/operators", response_model=PageOut[AdminUserListOut])
async def list_operators(
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    sort_by: str = Query("created_at", pattern="^(created_at|full_name|phone|email)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminUserService(session).list_users(
        query=search, is_active=is_active, role="operator",
        sort_by=sort_by, sort_order=sort_order,
        page=page, size=size,
    )


# Администраторы (role=admin) — только суперадмин
@router.get("/admin/admins", response_model=PageOut[AdminUserListOut])
async def list_admins(
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    sort_by: str = Query("created_at", pattern="^(created_at|full_name|phone|email)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.SUPERADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminUserService(session).list_users(
        query=search, is_active=is_active, role="admin",
        sort_by=sort_by, sort_order=sort_order,
        page=page, size=size,
    )

# Подробнее о пользователе
@router.get("/admin/users/{user_id}", response_model=AdminUserDetailOut)
async def get_user(
    user_id: int,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminUserService(session).get_user_detail(user_id)

# Подтвердить аккаунт
@router.post("/admin/users/{user_id}/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_user(
    user_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminUserService(session).verify_user(user_id, actor.id, actor_role)

# Снять подтверждение
@router.post("/admin/users/{user_id}/unverify", status_code=status.HTTP_204_NO_CONTENT)
async def unverify_user(
    user_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminUserService(session).unverify_user(user_id, actor.id, actor_role)

# Забанить
@router.post("/admin/users/{user_id}/ban", status_code=status.HTTP_204_NO_CONTENT)
async def ban_user(
    user_id: int,
    payload: BanUserIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminUserService(session).ban_user(user_id, payload.reason, actor.id, actor_role)

# Удалить оператора
@router.delete("/admin/users/operators/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_operator(
    user_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminUserService(session).delete_operator(user_id, actor.id, actor_role)


# Разбанить
@router.post("/admin/users/{user_id}/unban", status_code=status.HTTP_204_NO_CONTENT)
async def unban_user(
    user_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminUserService(session).unban_user(user_id, actor.id, actor_role)

# Все пользователи подвязанные к шкафу
@router.get("/admin/cabinets/{cabinet_id}/users", response_model=list[CabinetUserOut])
async def list_cabinet_users(
    cabinet_id: int,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminUserService(session).list_cabinet_users(cabinet_id)

# Удалить пользователя со шкафа
@router.delete(
    "/admin/cabinets/{cabinet_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_user_from_cabinet(
    cabinet_id: int,
    user_id: int,
    payload: RemoveUserFromCabinetIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminUserService(session).remove_user_from_cabinet(cabinet_id, user_id, payload.reason, actor.id, actor_role)
