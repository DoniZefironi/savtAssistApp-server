from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_current_user, get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.notifications import (
    BroadcastIn,
    DeviceTokenIn,
    NotificationOut,
    NotificationSettingsOut,
    NotificationSettingsPatchIn,
)
from app.schemas.pagination import PageOut
from app.services.notification_service import NotificationService

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=PageOut[NotificationOut])
async def list_notifications(
    is_read: bool | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await NotificationService(session).list_notifications(
        current_user.id, is_read, page, size
    )


@router.post("/notifications/{notif_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notif_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await NotificationService(session).mark_read(current_user.id, notif_id)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await NotificationService(session).mark_all_read(current_user.id)


@router.get("/notifications/settings", response_model=NotificationSettingsOut)
async def get_settings(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await NotificationService(session).get_settings(current_user.id)


@router.patch("/notifications/settings", response_model=NotificationSettingsOut)
async def update_settings(
    payload: NotificationSettingsPatchIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await NotificationService(session).update_settings(current_user.id, payload)


@router.post("/device-tokens", status_code=status.HTTP_204_NO_CONTENT)
async def register_device(
    payload: DeviceTokenIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await NotificationService(session).register_device(current_user.id, payload)


@router.delete("/device-tokens/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_device(
    token: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await NotificationService(session).remove_device(current_user.id, token)


@router.post("/admin/notifications/broadcast", status_code=status.HTTP_204_NO_CONTENT)
async def broadcast(
    payload: BroadcastIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await NotificationService(session).broadcast(payload, actor.id, actor_role)
