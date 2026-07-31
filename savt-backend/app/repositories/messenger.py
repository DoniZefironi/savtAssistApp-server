from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.messenger_link import MessengerLink


class MessengerLinkRepository:
    """Подтверждённые связки аккаунт ↔ чат Telegram.

    Заводятся только при регистрации, где номер доказан контактом из Telegram
    (см. messenger_webhook_service). Сброс пароля новых связок не создаёт: этот
    эндпоинт открыт без авторизации, и раньше он выдавал токен привязки прямо в
    ответе — чего хватало, чтобы привязать свой мессенджер к чужому аккаунту и
    получить код сброса пароля."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_by_user_and_channel(self, user_id: int, channel: str) -> MessengerLink | None:
        result = await self.session.execute(
            select(MessengerLink).where(
                MessengerLink.user_id == user_id,
                MessengerLink.channel == channel,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, user_id: int, channel: str, external_chat_id: str) -> MessengerLink:
        existing = await self.find_by_user_and_channel(user_id, channel)
        if existing is not None:
            existing.external_chat_id = external_chat_id
            existing.linked_at = datetime.now(timezone.utc)
            return existing
        obj = MessengerLink(user_id=user_id, channel=channel, external_chat_id=external_chat_id)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete_by_user_and_channel(self, user_id: int, channel: str) -> None:
        existing = await self.find_by_user_and_channel(user_id, channel)
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()
