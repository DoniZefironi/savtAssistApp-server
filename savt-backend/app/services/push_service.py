import asyncio
import logging

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.firebase import is_firebase_ready
from app.models.device_token import DeviceToken
from app.models.notification_settings import NotificationSettings
from firebase_admin import messaging

logger = logging.getLogger(__name__)

# Какой переключатель в настройках уведомлений отвечает за какой тип пуша.
# Типы, которых здесь нет (например "operator_requested" — служебный сигнал
# операторам), отправляются всегда.
_SETTING_BY_TYPE: dict[str, str] = {
    "chat_message": "chat_messages",
    "request_status": "request_status_change",
    "warranty_expiring": "warranty_expiring",
    "promotional": "promotional",
    "cabinet_alarm": "cabinet_alarms",
}


async def _is_allowed(session: AsyncSession, user_id: int, notification_type: str | None) -> bool:
    """Проверка настроек уведомлений живёт здесь, а не только в NotificationService.

    Чаты зовут send_push напрямую (сообщение в чате не заводит запись в списке
    уведомлений — у чатов свои счётчики непрочитанного), поэтому проверка внутри
    NotificationService их не покрывала: выключенный переключатель «уведомления о
    сообщениях» ни на что не влиял.

    Здесь же — временная пауза. Именно здесь, а не в NotificationService: пауза
    должна глушить и то, что идёт мимо него (сообщения в чатах, ответы бота,
    сигнал операторам), иначе «не беспокоить» беспокоило бы ровно тем, чем чаще
    всего и беспокоят."""
    settings = await session.get(NotificationSettings, user_id)
    if settings is None:
        return True  # строки нет — действуют значения по умолчанию из модели

    if settings.is_muted():
        return False

    field = _SETTING_BY_TYPE.get(notification_type or "")
    if field is None:
        return True
    return bool(getattr(settings, field, True))


async def send_push(
    session: AsyncSession,
    user_id: int,
    title: str,
    body: str,
    data: dict | None = None,
    notification_type: str | None = None,
) -> None:
    if not is_firebase_ready():
        return

    if not await _is_allowed(session, user_id, notification_type):
        return

    result = await session.execute(
        select(DeviceToken.token).where(DeviceToken.user_id == user_id)
    )
    tokens = [row[0] for row in result.all()]
    if not tokens:
        return

    try:
        messages = [
            messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={k: str(v) for k, v in (data or {}).items()},
                token=token,
            )
            for token in tokens
        ]
        # send_each — синхронный сетевой вызов. Без to_thread он блокирует весь
        # event loop (uvicorn запущен одним воркером), и отправка сообщения в чате
        # ждала бы ответа FCM по каждому устройству получателя.
        response = await asyncio.to_thread(messaging.send_each, messages)
        logger.info(f"Push отправлен {user_id}: {response.success_count} успешно, {response.failure_count} ошибок")

        failed = {
            messages[i].token
            for i, r in enumerate(response.responses)
            if not r.success and r.exception and "registration-token-not-registered" in str(r.exception)
        }
        if failed:
            await _drop_dead_tokens(failed)
    except Exception as e:
        logger.error(f"Ошибка отправки push для {user_id}: {e}")


async def _drop_dead_tokens(tokens: set[str]) -> None:
    """Удаляет отозванные FCM-токены в своей сессии.

    Своя — потому что вызывающие зовут send_push в разных точках транзакции:
    часть уже закоммитила, часть нет (bot_service шлёт пуш до коммита своего
    сообщения). Коммит в чужой сессии закоммитил бы и их незавершённую работу,
    а без коммита удаление откатывалось бы — как и было раньше, из-за чего
    мёртвые токены копились вечно и каждая отправка била в FCM впустую."""
    from app.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(sa_delete(DeviceToken).where(DeviceToken.token.in_(tokens)))
            await session.commit()
        logger.info("Удалено отозванных FCM-токенов: %d", len(tokens))
    except Exception:
        logger.exception("Не удалось удалить отозванные FCM-токены")
