import secrets
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
from app.repositories.messenger import MessengerLinkRepository, MessengerLinkRequestRepository
from app.repositories.user import UserRepository
from app.services import messenger_service
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
        self.messenger_link_repo = MessengerLinkRepository(session) # подключённые Telegram/Viber
        self.messenger_link_request_repo = MessengerLinkRequestRepository(session) # ожидающие рукопожатия

    # Начальная регистрация, вводим все поля после запрашиваем код.
    # channel — "telegram" | "viber", куда доставить код (SMS отключено полностью)
    async def register_start(
        self,
        phone: str,
        password: str,
        full_name: str | None,
        user_type: str,
        organization_name: str | None,
        channel: str,
    ) -> tuple[int, str | None]:

        # Проверяем, может уже есть такой телефончек
        existing_user = await self.user_repo.find_by_phone(phone)

        if existing_user is not None and existing_user.is_phone_verified:
            raise AlreadyExistsError(
                "Пользователь с таким телефоном уже зарегистрирован"
            )

        # Проверяем кулдаун, чтоб не спамили школьники
        remaining = await self._resend_cooldown_remaining(phone, PURPOSE_REGISTRATION)
        if remaining > 0:
            raise RateLimitError(f"Повторная отправка возможна через {remaining} сек.")

        # Если пользователя нет - создаем(телефон не подтвержден)
        if existing_user is None:
            user = await self.user_repo.create(
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
            user = existing_user

        # Отправляем код в Telegram/Viber — либо сразу (канал уже подключён),
        # либо возвращаем deep-link на рукопожатие с ботом (см. _start_verification)
        return await self._start_verification(phone, PURPOSE_REGISTRATION, channel, user.id)

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
        from app.services.chat_service import ChatService, chat_summary_dict
        support_chat = await ChatService(self.session).ensure_support_and_notes(user.id)

        # Выдаем токен
        access, refresh = await self._issue_tokens(user, user_agent, ip_address)
        await self.session.commit()

        if support_chat is not None:
            from app.services.realtime_events import publish_chat_created
            await publish_chat_created(support_chat.id, chat_summary_dict(support_chat, user_name=user.full_name))

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
        if role is None or role.name not in (RoleName.ADMIN.value, RoleName.OPERATOR.value, RoleName.SUPERADMIN.value):
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

    # Обновление токена (как же не понятно по названию)
    async def refresh_tokens(
        self,
        refresh_token: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:
        token_hash = hash_token(refresh_token)
        stored = await self.token_repo.find_by_hash(token_hash, for_update=True)

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

    # Удаление аккаунта
    async def delete_account(self, user: User) -> None:
        await self.token_repo.revoke_all_for_user(user.id)
        await self.session.delete(user)
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
    async def password_reset_start(self, phone: str, channel: str) -> tuple[int, str | None]:

        cooldown = settings.sms_code_resend_cooldown_seconds

        # Тихо, неспеша, проверяем существование пользователя, даже если он не найден, то все равно шлем cooldown
        # без deep_link (чтоб школьники не перебирали телефоны и не отличали "нет такого" от "есть, но лимит")
        user = await self.user_repo.find_by_phone(phone)

        if user is None or not user.is_active or not user.is_phone_verified:
            return cooldown, None

        remaining = await self._resend_cooldown_remaining(phone, PURPOSE_PASSWORD_RESET)
        if remaining > 0:
            return cooldown, None

        return await self._start_verification(phone, PURPOSE_PASSWORD_RESET, channel, user.id)

    # Устанавливаем новый пароль
    async def password_reset_complete(
        self,
        phone: str,
        code: str,
        new_password: str,
        new_password_confirm: str
    ) -> None:
        if new_password != new_password_confirm:
            raise InvalidCodeError("Новые пароли не совпадают")

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
    async def register_resend_code(self, phone: str, channel: str) -> tuple[int, str | None]:

        user = await self.user_repo.find_by_phone(phone)
        if user is None:
            raise NotFoundError("Сначала запустите регистрацию")

        if user.is_phone_verified:
            raise AlreadyExistsError("Пользователь уже подтверждён")

        remaining = await self._resend_cooldown_remaining(phone, PURPOSE_REGISTRATION)
        if remaining > 0:
            raise RateLimitError(f"Повторная отправка возможна через {remaining} сек.")

        return await self._start_verification(phone, PURPOSE_REGISTRATION, channel, user.id)
    
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
            raise InvalidCodeError("Новый пароль должен отличаться от предыдущего")

        if new_password != new_password_confirm:
            raise InvalidCodeError("Новый пароль и подтверждение не совпадают")

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
    async def change_phone_start(self, user: User, new_phone: str, channel: str) -> tuple[int, str | None]:
        if user.phone == new_phone:
            raise AlreadyExistsError("Это уже ваш текущий номер")
        existing = await self.user_repo.find_by_phone(new_phone)
        if existing is not None:
            raise AlreadyExistsError("Этот номер уже занят другим пользователем")

        remaining = await self._resend_cooldown_remaining(new_phone, PURPOSE_PHONE_CHANGE)
        if remaining > 0:
            raise RateLimitError(f"Повторная отправка возможна через {remaining} сек.")

        # user.id — если Telegram/Viber уже подключён у этого аккаунта (например, во время
        # регистрации), рукопожатие заново проходить не нужно, код улетит сразу
        return await self._start_verification(new_phone, PURPOSE_PHONE_CHANGE, channel, user.id)

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

    # Сколько секунд осталось до следующей попытки — смотрим и на уже отправленные
    # коды, и на незавершённые заявки на рукопожатие с ботом (иначе можно обойти
    # кулдаун, просто переключившись на другой мессенджер посреди ожидания)
    async def _resend_cooldown_remaining(self, phone: str, purpose: str) -> int:
        cooldown = settings.sms_code_resend_cooldown_seconds
        candidates: list[datetime] = []

        latest_code = await self.code_repo.find_latest(phone, purpose)
        if latest_code is not None:
            candidates.append(latest_code.created_at)

        latest_request = await self.messenger_link_request_repo.find_latest(phone, purpose)
        if latest_request is not None:
            candidates.append(latest_request.created_at)

        if not candidates:
            return 0

        elapsed = (datetime.now(timezone.utc) - max(candidates)).total_seconds()
        remaining = cooldown - elapsed
        return int(remaining) if remaining > 0 else 0

    # Общая точка отправки/переотправки кода для всех 4 сценариев (регистрация,
    # переотправка, сброс пароля, смена телефона). Если канал (Telegram/Viber) уже
    # подключён у этого пользователя — код генерируется и шлётся сразу, как раньше
    # с SMS. Если нет — код вообще не создаётся (нечего было бы доставить), вместо
    # этого заводим заявку на рукопожатие и возвращаем deep-link на бота; сам код
    # будет сгенерирован и отправлен только когда бот получит /start (см.
    # app/services/messenger_webhook_service.py).
    async def _start_verification(
        self, phone: str, purpose: str, channel: str, user_id: int,
    ) -> tuple[int, str | None]:
        cooldown = settings.sms_code_resend_cooldown_seconds

        link = await self.messenger_link_repo.find_by_user_and_channel(user_id, channel)
        if link is not None:
            await self._deliver_code(user_id, phone, purpose, channel, link.external_chat_id)
            return cooldown, None

        token = secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.messenger_link_request_ttl_minutes
        )
        await self.messenger_link_request_repo.create(
            token=token, user_id=user_id, phone=phone, purpose=purpose,
            channel=channel, expires_at=expires_at,
        )
        await self.session.commit()
        return cooldown, messenger_service.build_deep_link(channel, token)

    # Публичная обёртка над _deliver_code — вызывается из messenger_webhook_service.py
    # сразу после того, как бот подтвердил рукопожатие (получил /start с токеном)
    async def deliver_code_after_link(
        self, user_id: int, phone: str, purpose: str, channel: str, external_chat_id: str,
    ) -> None:
        await self._deliver_code(user_id, phone, purpose, channel, external_chat_id)

    # Генерирует код, сохраняет хэш и шлёт его в уже подключённый канал —
    # вызывается и отсюда (канал был подключён заранее), и из вебхука бота
    # (сразу после того, как рукопожатие только что подтвердилось)
    async def _deliver_code(
        self, user_id: int, phone: str, purpose: str, channel: str, external_chat_id: str,
    ) -> None:
        code = generate_sms_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.sms_code_ttl_minutes)
        await self.code_repo.create(
            phone=phone,
            code_hash=hash_token(code),
            purpose=purpose,
            expires_at=expires_at,
            max_attempts=settings.sms_code_max_attempts,
        )
        try:
            await messenger_service.send_verification_code(channel, external_chat_id, code)
        except messenger_service.MessengerSendError:
            # Бот заблокирован/чат недоступен — чистим связку, чтобы следующий
            # запрос кода заново запустил рукопожатие, а не бился в ту же стену
            await self.messenger_link_repo.delete_by_user_and_channel(user_id, channel)
            raise
        await self.session.commit()
