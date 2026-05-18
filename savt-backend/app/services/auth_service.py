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
from app.models.role import Role


_DEFAULT_USER_ROLE_ID = 1

PURPOSE_REGISTRATION = "registration"
PURPOSE_PASSWORD_RESET = "password_reset"
PURPOSE_PHONE_CHANGE = "phone_change"


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session) # работа с пользователем
        self.code_repo = PhoneCodeRepository(session) # работа с кодами
        self.token_repo = RefreshTokenRepository(session) # работа с токеном

    # Начальная регистрация, вводим все поля после запрашиваем код
    async def register_start(
        self,
        phone: str,
        password: str,
        full_name: str | None,
        user_type: str,
        organization_name: str | None
    ) -> int:

        # Проверяем, может уже есть такой телефончек
        existing_user = await self.user_repo.find_by_phone(phone)

        if existing_user is not None and existing_user.is_phone_verified:
            raise AlreadyExistsError(
                "Пользователь с таким телефоном уже зарегистрирован"
            )

        # Проверяем кулдаун, чтоб не спамили школьники
        latest = await self.code_repo.find_latest(phone, PURPOSE_REGISTRATION)
        if latest is not None:
            elapsed = (datetime.now(timezone.utc) - latest.created_at).total_seconds()
            cooldown = settings.sms_code_resend_cooldown_seconds
            if elapsed < cooldown:
                raise RateLimitError(
                    f"Повторная отправка возможна через {int(cooldown - elapsed)} сек."
                )

        # Если пользователя нет - создаем(телефон не подтвержден)
        if existing_user is None:
            await self.user_repo.create(
                phone=phone,
                full_name=full_name,
                hashed_password=hash_password(password),
                role_id=_DEFAULT_USER_ROLE_ID,
                is_phone_verified=False,
                is_active=True,
                user_type=user_type,
                organization_name=organization_name
            )
        else:
            # Если есть такой пользователь, но телефон не подтвержден - задаем новые значения полей
            existing_user.hashed_password = hash_password(password)
            existing_user.full_name = full_name
            existing_user.user_type = user_type
            existing_user.organization_name = organization_name

        # Генерируем и создаем кодик
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

        # Отправляем эсэмэску
        await sms_service.send_verification_code(phone, code)

        await self.session.commit()
        return settings.sms_code_resend_cooldown_seconds

    # Подтверждение телефона
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

        # Проверяем пользователя
        if user.is_phone_verified:
            raise AlreadyExistsError("Пользователь уже подтверждён, используйте логин")

        # Проверка кода
        active_code = await self.code_repo.find_active(phone, PURPOSE_REGISTRATION)

        if active_code is None:
            raise InvalidCodeError("Код не найден или истёк")

        # Защищаемся от школьников
        if active_code.attempts >= active_code.max_attempts:
            raise InvalidCodeError("Превышено число попыток. Запросите новый код")

        # Сравниваем хэш кодов
        if hash_token(code) != active_code.code_hash:
            await self.code_repo.increment_attempts(active_code)
            await self.session.commit()
            raise InvalidCodeError("Неверный код")

        # Успешно - код использован, телефон подтвержден
        await self.code_repo.mark_used(active_code)
        user.is_phone_verified = True

        # Создаём базовые чаты
        from app.services.chat_service import ChatService
        await ChatService(self.session).ensure_support_and_notes(user.id)

        # Выдаем токен
        access, refresh = await self._issue_tokens(user, user_agent, ip_address)
        await self.session.commit()
        return access, refresh 

    # Вход для администратора / оператора через логин
    async def admin_login(
        self,
        login: str,
        password: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        user = await self.user_repo.find_by_login(login)

        if user is None or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Неверный логин или пароль")

        if not user.is_active:
            raise AuthenticationError("Аккаунт заблокирован")

        role = await self.session.get(Role, user.role_id)
        if role is None or role.name not in (RoleName.ADMIN.value, RoleName.OPERATOR.value):
            raise AuthenticationError("Недостаточно прав для входа через этот endpoint")

        await self.token_repo.trim_sessions(user.id, max_sessions=5)

        access, refresh = await self._issue_tokens(user, user_agent, ip_address)
        await self.session.commit()
        return access, refresh

    # Вход(как неочевидно по названию)
    async def login(
        self,
        phone: str,
        password: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        user = await self.user_repo.find_by_phone(phone)

        # Проверяем пароль, сравниваем хэши
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Неверный телефон или пароль")

        # Проверяем актиивность аккаунта, может в бане школьник
        if not user.is_active:
            raise AuthenticationError("Аккаунт заблокирован")

        if not user.is_phone_verified:
            raise AuthenticationError("Телефон не подтверждён")

        # Убираем старейшие сессии если их больше 5
        await self.token_repo.trim_sessions(user.id, max_sessions=5)

        # Выдаем токен
        access, refresh = await self._issue_tokens(user, user_agent, ip_address)
        await self.session.commit()
        return access, refresh

    # Вход для админа
    async def loginAdmin(
        self,
        login: str,
        password: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        user = await self.user_repo.find_by_login(login)

        # Проверяем пароль, сравниваем хэши
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Неверный логин или пароль")

        # Проверяем актиивность аккаунта, может в бане школьник
        if not user.is_active:
            raise AuthenticationError("Аккаунт заблокирован")

        # Выдаем токен
        access, refresh = await self._issue_tokens(user, user_agent, ip_address)
        await self.session.commit()
        return access, refresh

    # Обновление токена (как же не понятно по названию)
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
            # токена уже нет, кто-то хочет его использовать(ну или просто со всех устройств вышли, и токен остался в кеше)
            await self.token_repo.revoke_all_for_user(stored.user_id)
            await self.session.commit()
            raise AuthenticationError(
                "Токен скомпрометирован. Все сессии завершены, войдите заново"
            )

        # Проверка не истек ли токен
        if stored.expires_at < datetime.now(timezone.utc):
            raise AuthenticationError("Refresh-токен истёк")

        user = await self.user_repo.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Пользователь недоступен")

        # Создаем новую пару токенов
        new_access, new_refresh, new_refresh_obj = await self._issue_tokens_internal(
            user, user_agent, ip_address
        )

        # Отзываем старый токен и связываем с новым
        await self.token_repo.revoke(stored, replaced_by_id=new_refresh_obj.id)
        stored.last_used_at = datetime.now(timezone.utc)

        await self.session.commit()
        return new_access, new_refresh

    # Выход из аккаунта
    async def logout(self, refresh_token: str) -> None:
        token_hash = hash_token(refresh_token)
        stored = await self.token_repo.find_by_hash(token_hash)
        if stored is not None and stored.revoked_at is None:
            await self.token_repo.revoke(stored) # отзываем токен
        await self.session.commit()

    
    async def _issue_tokens(
        self,
        user: User,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        access, refresh, _ = await self._issue_tokens_internal(user, user_agent, ip_address)
        return access, refresh

    # Выдача токенов
    async def _issue_tokens_internal(
        self,
        user: User,
        user_agent: str | None,
        ip_address: str | None,
    ):
        # получаем роль пользователя
        role = await self.session.get(Role, user.role_id)
        role_name = role.name if role else RoleName.USER.value

        access_token = create_access_token(user_id=user.id, role=role_name)

        refresh_token = generate_refresh_token()
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.jwt_refresh_token_ttl_days
        )
        # сохраняем хэщ с данными
        refresh_obj = await self.token_repo.create(
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return access_token, refresh_token, refresh_obj

    # Сброс пароля (почти как регистрация)
    async def password_reset_start(self, phone: str) -> int:

        cooldown = settings.sms_code_resend_cooldown_seconds

        # Тихо, неспеша, проверяем существование пользователя, даже если он не найдет, то все равно шлем cooldown(чтоб школьники не перебирали телефоны)
        user = await self.user_repo.find_by_phone(phone)

        if user is None or not user.is_active or not user.is_phone_verified:
            return cooldown

        latest = await self.code_repo.find_latest(phone, PURPOSE_PASSWORD_RESET)
        if latest is not None:
            elapsed = (datetime.now(timezone.utc) - latest.created_at).total_seconds()
            if elapsed < cooldown:

                return cooldown

        code = generate_sms_code()
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.sms_code_ttl_minutes
        )
        await self.code_repo.create(
            phone=phone,
            code_hash=hash_token(code),
            purpose=PURPOSE_PASSWORD_RESET,
            expires_at=expires_at,
            max_attempts=settings.sms_code_max_attempts,
        )
        await sms_service.send_verification_code(phone, code)
        await self.session.commit()
        return cooldown

    # Устанавливаем новый пароль
    async def password_reset_complete(
        self,
        phone: str,
        code: str,
        new_password: str,
        new_password_confirm: str
    ) -> None:
        if new_password != new_password_confirm:
            raise ValueError("Новые пароли не совпадают")

        user = await self.user_repo.find_by_phone(phone)
        if user is None or not user.is_active or not user.is_phone_verified:
            raise InvalidCodeError("Код не найден или истёк")

        active_code = await self.code_repo.find_active(phone, PURPOSE_PASSWORD_RESET)
        if active_code is None:
            raise InvalidCodeError("Код не найден или истёк")

        if active_code.attempts >= active_code.max_attempts:
            raise InvalidCodeError("Превышено число попыток. Запросите новый код")

        if hash_token(code) != active_code.code_hash:
            await self.code_repo.increment_attempts(active_code)
            await self.session.commit()
            raise InvalidCodeError("Неверный код")

        await self.code_repo.mark_used(active_code)
        user.hashed_password = hash_password(new_password)

        await self.token_repo.revoke_all_for_user(user.id)

        await self.session.commit()

    # Повторно код запросить
    async def register_resend_code(self, phone: str) -> int:

        user = await self.user_repo.find_by_phone(phone)
        if user is None:
            raise NotFoundError("Сначала запустите регистрацию")

        if user.is_phone_verified:
            raise AlreadyExistsError("Пользователь уже подтверждён")

        latest = await self.code_repo.find_latest(phone, PURPOSE_REGISTRATION)
        cooldown = settings.sms_code_resend_cooldown_seconds
        if latest is not None:
            elapsed = (datetime.now(timezone.utc) - latest.created_at).total_seconds()
            if elapsed < cooldown:
                raise RateLimitError(
                    f"Повторная отправка возможна через {int(cooldown - elapsed)} сек."
                )

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
        return cooldown
    
    # смена пароля
    async def change_password(
            self, 
            user: User, 
            old_password: str, 
            new_password: str, 
            new_password_confirm: str
            ) -> None:
        
        if not verify_password(old_password, user.hashed_password):
            raise AuthenticationError("Неверный текущий пароль")
        
        if old_password == new_password:
            raise ValueError("Новый пароль должен отличаться от предыдущего")
        
        if new_password != new_password_confirm:
            raise ValueError("Новый пароль и подтверждение не совпадают")

        user.hashed_password = hash_password(new_password)
        await self.token_repo.revoke_all_for_user(user.id)
        await self.session.commit()

    # Обновление профиля
    async def update_profile(
        self,
        user: User,
        full_name: str | None,
        email: str | None,
        organization_name: str | None,
    ) -> User:
        if full_name is not None:
            user.full_name = full_name
        if email is not None:
            user.email = email
        if organization_name is not None:
            user.organization_name = organization_name
        await self.session.commit()
        return user

    # Смена номера — шаг 1: код на новый номер
    async def change_phone_start(self, user: User, new_phone: str) -> int:
        if user.phone == new_phone:
            raise AlreadyExistsError("Это уже ваш текущий номер")
        existing = await self.user_repo.find_by_phone(new_phone)
        if existing is not None:
            raise AlreadyExistsError("Этот номер уже занят другим пользователем")
        latest = await self.code_repo.find_latest(new_phone, PURPOSE_PHONE_CHANGE)
        cooldown = settings.sms_code_resend_cooldown_seconds
        if latest is not None:
            elapsed = (datetime.now(timezone.utc) - latest.created_at).total_seconds()
            if elapsed < cooldown:
                raise RateLimitError(
                    f"Повторная отправка возможна через {int(cooldown - elapsed)} сек."
                )
        code = generate_sms_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.sms_code_ttl_minutes)
        await self.code_repo.create(
            phone=new_phone,
            code_hash=hash_token(code),
            purpose=PURPOSE_PHONE_CHANGE,
            expires_at=expires_at,
            max_attempts=settings.sms_code_max_attempts,
        )
        await sms_service.send_verification_code(new_phone, code)
        await self.session.commit()
        return cooldown

    # Смена номера — шаг 2: подтвердить и сменить
    async def change_phone_complete(self, user: User, new_phone: str, code: str) -> None:
        existing = await self.user_repo.find_by_phone(new_phone)
        if existing is not None:
            raise AlreadyExistsError("Этот номер уже занят другим пользователем")
        active_code = await self.code_repo.find_active(new_phone, PURPOSE_PHONE_CHANGE)
        if active_code is None:
            raise InvalidCodeError("Код не найден или истёк")
        if active_code.attempts >= active_code.max_attempts:
            raise InvalidCodeError("Превышено число попыток. Запросите новый код")
        if hash_token(code) != active_code.code_hash:
            await self.code_repo.increment_attempts(active_code)
            await self.session.commit()
            raise InvalidCodeError("Неверный код")
        await self.code_repo.mark_used(active_code)
        user.phone = new_phone
        await self.session.commit()
