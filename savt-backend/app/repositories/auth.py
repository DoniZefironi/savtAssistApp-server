from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_verification_code import PhoneVerificationCode
from app.models.refresh_token import RefreshToken


class PhoneCodeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        phone: str,
        code_hash: str,
        purpose: str,
        expires_at: datetime,
        max_attempts: int,
    ) -> PhoneVerificationCode:
        obj = PhoneVerificationCode(
            phone=phone,
            code_hash=code_hash,
            purpose=purpose,
            expires_at=expires_at,
            max_attempts=max_attempts,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def find_active(self, phone: str, purpose: str) -> PhoneVerificationCode | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(PhoneVerificationCode)
            .where(
                PhoneVerificationCode.phone == phone,
                PhoneVerificationCode.purpose == purpose,
                PhoneVerificationCode.used_at.is_(None),
                PhoneVerificationCode.expires_at > now,
            )
            .order_by(PhoneVerificationCode.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_latest(self, phone: str, purpose: str) -> PhoneVerificationCode | None:
        result = await self.session.execute(
            select(PhoneVerificationCode)
            .where(
                PhoneVerificationCode.phone == phone,
                PhoneVerificationCode.purpose == purpose,
            )
            .order_by(PhoneVerificationCode.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def increment_attempts(self, code: PhoneVerificationCode) -> None:
        code.attempts += 1

    async def mark_used(self, code: PhoneVerificationCode) -> None:
        code.used_at = datetime.now(timezone.utc)


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
        ip_address: str | None,
    ) -> RefreshToken:
        obj = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke(self, token: RefreshToken, replaced_by_id: int | None = None) -> None:
        token.revoked_at = datetime.now(timezone.utc)
        if replaced_by_id is not None:
            token.replaced_by_id = replaced_by_id

    async def revoke_all_for_user(self, user_id: int) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )