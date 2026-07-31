from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pending_registration import PendingRegistration


class PendingRegistrationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        token: str,
        hashed_password: str,
        full_name: str | None,
        user_type: str | None,
        organization_name: str | None,
        contact_phone: str | None,
        expires_at: datetime,
    ) -> PendingRegistration:
        obj = PendingRegistration(
            token=token,
            hashed_password=hashed_password,
            full_name=full_name,
            user_type=user_type,
            organization_name=organization_name,
            contact_phone=contact_phone,
            expires_at=expires_at,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def find_by_token(self, token: str) -> PendingRegistration | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(PendingRegistration).where(
                PendingRegistration.token == token,
                PendingRegistration.consumed_at.is_(None),
                PendingRegistration.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    # Второй шаг: в апдейте с контактом токена нет, ищем по чату из /start
    async def find_by_chat(self, external_chat_id: str) -> PendingRegistration | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(PendingRegistration)
            .where(
                PendingRegistration.external_chat_id == external_chat_id,
                PendingRegistration.consumed_at.is_(None),
                PendingRegistration.expires_at > now,
            )
            .order_by(PendingRegistration.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def mark_consumed(self, obj: PendingRegistration) -> None:
        obj.consumed_at = datetime.now(timezone.utc)
