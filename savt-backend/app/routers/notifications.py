from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_current_user, get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.notifications import (
    BroadcastIn,
    DeviceTokenIn,
    MuteIn,
    NotificationOut,
    NotificationSettingsOut,
    NotificationSettingsPatchIn,
    PromoMessageOut,
    PromoSendResultOut,
    UnreadCountOut,
)
from app.schemas.pagination import PageOut
from app.services import promo_service
from app.services.audit_service import AuditLogger
from app.services.notification_service import NotificationService

router = APIRouter(tags=["notifications"])


# История уведомлений пользователя: смены статусов заявок, гарантия, реклама.
# Сообщений в чатах здесь нет — они push-only, у чатов свои счётчики непрочитанного.
@router.get("/notifications", response_model=PageOut[NotificationOut])
async def list_notifications(
    is_read: bool | None = Query(None),
    # Повторяемый параметр: ?type=request_status&type=warranty_expiring
    type: list[str] = Query(default=[]),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await NotificationService(session).list_notifications(
        current_user.id, is_read, page, size, types=type or None
    )


# Для бейджа на иконке — дешевле, чем тянуть первую страницу списка
@router.get("/notifications/unread-count", response_model=UnreadCountOut)
async def unread_count(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return UnreadCountOut(unread=await NotificationService(session).unread_count(current_user.id))


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


@router.delete("/notifications", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_notifications(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await NotificationService(session).delete_all(current_user.id)


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


# Пауза: глушит ВСЕ пуши, включая сообщения в чатах, независимо от переключателей
# по типам. История при этом продолжает копиться — пользователь просил не
# беспокоить, а не выбрасывать уведомления.
@router.post("/notifications/mute", response_model=NotificationSettingsOut)
async def mute_notifications(
    payload: MuteIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await NotificationService(session).mute(current_user.id, payload.hours)


@router.delete("/notifications/mute", response_model=NotificationSettingsOut)
async def unmute_notifications(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await NotificationService(session).unmute(current_user.id)


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


# --- рекламные заготовки (PROMO_MESSAGES_FILE) ---

@router.get("/admin/notifications/promo", response_model=list[PromoMessageOut])
async def list_promo_messages(
    _: User = Depends(require_role(RoleName.ADMIN)),
):
    """Что сейчас лежит в подборке. Файл читается заново, так что список сразу
    показывает результат правок."""
    return promo_service.load_messages()


@router.post("/admin/notifications/promo/send", response_model=PromoSendResultOut)
async def send_promo(
    role: str | None = Query(None, pattern="^(user|operator|admin)$"),
    promo_id: str | None = Query(None),
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    """Разослать рекламу из подборки: случайную либо конкретную по promo_id.
    Уважает переключатель promotional, как и обычная рассылка."""
    chosen = None
    if promo_id:
        chosen = next((m for m in promo_service.load_messages() if m.id == promo_id), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail=f"Заготовка {promo_id!r} не найдена")

    message, sent, skipped = await promo_service.send_random(session, role=role, message=chosen)
    if message is None:
        raise HTTPException(
            status_code=400,
            detail="Подборка рекламы пуста или файл не читается — см. PROMO_MESSAGES_FILE",
        )
    AuditLogger(session).log(
        "notification.promo_send", "notification", None, actor.id, actor_role,
        {"promo_id": message.id, "role": role, "recipients": sent, "opted_out": skipped},
    )
    await session.commit()
    return PromoSendResultOut(sent_to=sent, skipped_opted_out=skipped, message=message)
