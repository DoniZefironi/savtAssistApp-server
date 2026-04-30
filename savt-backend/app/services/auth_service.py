from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import RoleName
from app.core.exceptions import (
    AlreadyExistsError,
    AuthenticationError,
    InvalidCodeError,
    NotFoundError,
    RateLimitError,
)
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    generate_sms_code,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.user import User
from app.repositories.auth import PhoneCodeRepository, RefreshTokenRepository
from app.repositories.user import UserRepository
from app.services.sms_service import sms_service


_DEFAULT_USER_ROLE_ID = 1

PURPOSE_REGISTRATION = "registration"


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self.code_repo = PhoneCodeRepository(session)
        self.token_repo = RefreshTokenRepository(session)

    async def register_start(
        self,
        phone: str,
        password: str,
        full_name: str | None,
    ) -> int:

        existing_user = await self.user_repo.find_by_phone(phone)

        if existing_user is not None and existing_user.is_phone_verified:
            raise AlreadyExistsError(
                "Пользователь с таким телефоном уже зарегистрирован"
            )

        latest = await self.code_repo.find_latest(phone, PURPOSE_REGISTRATION)
        if latest is not None:
            elapsed = (datetime.now(timezone.utc) - latest.created_at).total_seconds()
            cooldown = settings.sms_code_resend_cooldown_seconds
            if elapsed < cooldown:
                raise RateLimitError(
                    f"Повторная отправка возможна через {int(cooldown - elapsed)} сек."
                )

        if existing_user is None:
            await self.user_repo.create(
                phone=phone,
                full_name=full_name,
                hashed_password=hash_password(password),
                role_id=_DEFAULT_USER_ROLE_ID,
                is_phone_verified=False,
                is_active=True,
            )
        else:
            existing_user.hashed_password = hash_password(password)
            existing_user.full_name = full_name

        code = generate_sms_code()
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.sms_code_ttl_minutes
        )
        await self.code_repo.create(
            phone=phone,
            code_hash=hash_token(code),
            purpose=PURPOSE_REGISTRATION,
            expires_at=expires_at,
            max_attempts=settings.sms_code_max_attempts,
        )

        await sms_service.send_verification_code(phone, code)

        await self.session.commit()
        return settings.sms_code_resend_cooldown_seconds


    async def register_complete(
        self,
        phone: str,
        code: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        user = await self.user_repo.find_by_phone(phone)
        if user is None:
            raise NotFoundError("Сначала запросите код регистрации")

        if user.is_phone_verified:
            raise AlreadyExistsError("Пользователь уже подтверждён, используйте логин")

        active_code = await self.code_repo.find_active(phone, PURPOSE_REGISTRATION)
        if active_code is None:
            raise InvalidCodeError("Код не найден или истёк")

        if active_code.attempts >= active_code.max_attempts:
            raise InvalidCodeError("Превышено число попыток. Запросите новый код")

        if hash_token(code) != active_code.code_hash:
            await self.code_repo.increment_attempts(active_code)
            await self.session.commit()
            raise InvalidCodeError("Неверный код")

        await self.code_repo.mark_used(active_code)
        user.is_phone_verified = True

        access, refresh = await self._issue_tokens(user, user_agent, ip_address)
        await self.session.commit()
        return access, refresh


    async def login(
        self,
        phone: str,
        password: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        user = await self.user_repo.find_by_phone(phone)


        if user is None or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Неверный телефон или пароль")

        if not user.is_active:
            raise AuthenticationError("Аккаунт заблокирован")

        if not user.is_phone_verified:
            raise AuthenticationError("Телефон не подтверждён")

        access, refresh = await self._issue_tokens(user, user_agent, ip_address)
        await self.session.commit()
        return access, refresh


    async def refresh_tokens(
        self,
        refresh_token: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        token_hash = hash_token(refresh_token)
        stored = await self.token_repo.find_by_hash(token_hash)

        if stored is None:
            raise AuthenticationError("Refresh-токен не найден")


        if stored.revoked_at is not None:
            await self.token_repo.revoke_all_for_user(stored.user_id)
            await self.session.commit()
            raise AuthenticationError(
                "Токен скомпрометирован. Все сессии завершены, войдите заново"
            )

        if stored.expires_at < datetime.now(timezone.utc):
            raise AuthenticationError("Refresh-токен истёк")

        user = await self.user_repo.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Пользователь недоступен")

        new_access, new_refresh, new_refresh_obj = await self._issue_tokens_internal(
            user, user_agent, ip_address
        )
        await self.token_repo.revoke(stored, replaced_by_id=new_refresh_obj.id)
        stored.last_used_at = datetime.now(timezone.utc)

        await self.session.commit()
        return new_access, new_refresh


    async def logout(self, refresh_token: str) -> None:
        token_hash = hash_token(refresh_token)
        stored = await self.token_repo.find_by_hash(token_hash)
        if stored is not None and stored.revoked_at is None:
            await self.token_repo.revoke(stored)
        await self.session.commit()


    async def _issue_tokens(
        self,
        user: User,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        access, refresh, _ = await self._issue_tokens_internal(user, user_agent, ip_address)
        return access, refresh

    async def _issue_tokens_internal(
        self,
        user: User,
        user_agent: str | None,
        ip_address: str | None,
    ):
        from app.models.roles import Role
        role = await self.session.get(Role, user.role_id)
        role_name = role.name if role else RoleName.USER.value

        access_token = create_access_token(user_id=user.id, role=role_name)

        refresh_token = generate_refresh_token()
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.jwt_refresh_token_ttl_days
        )
        refresh_obj = await self.token_repo.create(
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return access_token, refresh_token, refresh_obj