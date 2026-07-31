from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.messenger_link import MessengerLink
from app.models.messenger_link_request import MessengerLinkRequest


class MessengerLinkRepository:
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


class MessengerLinkRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        token: str,
        user_id: int,
        phone: str,
        purpose: str,
        channel: str,
        expires_at: datetime,
    ) -> MessengerLinkRequest:
        obj = MessengerLinkRequest(
            token=token,
            user_id=user_id,
            phone=phone,
            purpose=purpose,
            channel=channel,
            expires_at=expires_at,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def find_by_token(self, token: str) -> MessengerLinkRequest | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(MessengerLinkRequest).where(
                MessengerLinkRequest.token == token,
                MessengerLinkRequest.consumed_at.is_(None),
                MessengerLinkRequest.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    # Второй шаг рукопожатия: в апдейте с контактом токена уже нет, заявку ищем
    # по чату, который прислал /start (см. messenger_webhook_service)
    async def find_pending_by_chat(self, external_chat_id: str) -> MessengerLinkRequest | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(MessengerLinkRequest)
            .where(
                MessengerLinkRequest.external_chat_id == external_chat_id,
                MessengerLinkRequest.consumed_at.is_(None),
                MessengerLinkRequest.expires_at > now,
            )
            .order_by(MessengerLinkRequest.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # Кулдаун на переотправку — не важно, каким каналом пытались в прошлый раз,
    # чтобы нельзя было обойти лимит просто переключившись на другой мессенджер
    async def find_latest(self, phone: str, purpose: str) -> MessengerLinkRequest | None:
        result = await self.session.execute(
            select(MessengerLinkRequest)
            .where(
                MessengerLinkRequest.phone == phone,
                MessengerLinkRequest.purpose == purpose,
            )
            .order_by(MessengerLinkRequest.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_pending(self, user_id: int, channel: str, purpose: str) -> MessengerLinkRequest | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(MessengerLinkRequest)
            .where(
                MessengerLinkRequest.user_id == user_id,
                MessengerLinkRequest.channel == channel,
                MessengerLinkRequest.purpose == purpose,
                MessengerLinkRequest.consumed_at.is_(None),
                MessengerLinkRequest.expires_at > now,
            )
            .order_by(MessengerLinkRequest.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def mark_consumed(self, request: MessengerLinkRequest) -> None:
        request.consumed_at = datetime.now(timezone.utc)
