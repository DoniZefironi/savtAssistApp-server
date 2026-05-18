import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.firebase import is_firebase_ready
from app.models.device_token import DeviceToken
from firebase_admin import messaging

logger = logging.getLogger(__name__)


async def send_push(
    session: AsyncSession,
    user_id: int,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    if not is_firebase_ready():
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
        response = messaging.send_each(messages)
        logger.info(f"Push отправлен {user_id}: {response.success_count} успешно, {response.failure_count} ошибок")

        # Удаляем невалидные токены
        failed = {
            messages[i].token
            for i, r in enumerate(response.responses)
            if not r.success and r.exception and "registration-token-not-registered" in str(r.exception)
        }
        if failed:
            await session.execute(
                DeviceToken.__table__.delete().where(DeviceToken.token.in_(failed))
            )
    except Exception as e:
        logger.error(f"Ошибка отправки push для {user_id}: {e}")
