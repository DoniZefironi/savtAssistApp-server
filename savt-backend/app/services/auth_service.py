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
from app.repositories.messenger import MessengerLinkRepository
from app.repositories.pending_registration import PendingRegistrationRepository
from app.repositories.user import UserRepository
from app.services import messenger_service
from app.models.role import Role


_DEFAULT_USER_ROLE_ID = 1

PURPOSE_REGISTRATION = "registration"
PURPOSE_PASSWORD_RESET = "password_reset"
# "phone_change" здесь больше нет: смена номера идёт через заявку с одобрением
# админа (app/services/phone_change_service.py), кодов для неё не выпускается


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session) # работа с пользователем
        self.code_repo = PhoneCodeRepository(session) # работа с кодами
        self.token_repo = RefreshTokenRepository(session) # работа с токеном
        self.messenger_link_repo = MessengerLinkRepository(session) # подключённый Telegram
        self.pending_repo = PendingRegistrationRepository(session) # незавершённые регистрации

    # Начало регистрации. Номер телефона здесь НЕ запрашивается: он придёт из
    # контакта Telegram и потому будет подтверждён по построению. Данные формы
    # паркуются в pending_registrations — создать User без телефона не даёт
    # constraint ck_users_phone_or_login, да и незачем: пока номер неизвестен,
    # регистрировать нечего.
    async def register_start(
        self,
        password: str,
        full_name: str | None,
        user_type: str,
        organization_name: str | None,
        contact_phone: str | None,
    ) -> tuple[str, str, int]:
        token = secrets.token_urlsafe(24)
        # Живёт дольше рукопожатия: после подтверждения номера человеку нужно
        # ещё успеть ввести код, а он тоже со своим сроком
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.messenger_link_request_ttl_minutes + settings.sms_code_ttl_minutes
        )
        await self.pending_repo.create(
            token=token,
            hashed_password=hash_password(password),
            full_name=full_name,
            user_type=user_type,
            organization_name=organization_name,
            contact_phone=contact_phone,
            expires_at=expires_at,
        )
        await self.session.commit()
        deep_link = messenger_service.build_deep_link(messenger_service.CHANNEL_TELEGRAM, token)
        return token, deep_link, settings.sms_code_resend_cooldown_seconds

    # Подтверждение регистрации кодом. Идентифицируем не по телефону (клиент его
    # не знает — номер пришёл из Telegram), а по токену незавершённой регистрации.
    async def register_complete(
        self,
        registration_token: str,
        code: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, str]:

        pending = await self.pending_repo.find_by_token(registration_token)
        if pending is None:
            raise NotFoundError("Регистрация не найдена или истекла — начните заново")
        if pending.user_id is None:
            raise InvalidCodeError(
                "Номер ещё не подтверждён. Откройте бота в Telegram и нажмите "
                "«Отправить мой номер»"
            )

        user = await self.user_repo.get_by_id(pending.user_id)
        if user is None:
            raise NotFoundError("Регистрация не найдена или истекла — начните заново")
        phone = user.phone

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
        await self.pending_repo.mark_consumed(pending)
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

        # Код уйдёт, только если у пользователя есть подтверждённая связка с ботом.
        # Заводить её здесь нельзя: эндпоинт открыт без авторизации, и раньше при
        # её отсутствии deep-link с токеном привязки уходил прямо в ответе — то
        # есть атакующему, а следом к нему же и код сброса пароля. Если связки
        # нет, ответ снаружи неотличим от «такого номера нет» (см. выше).
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

    # Повторно код запросить. Идентификация по токену регистрации: телефон клиенту
    # неизвестен, пока пользователь не поделился контактом в Telegram.
    async def register_resend_code(self, registration_token: str) -> tuple[str | None, int]:
        pending = await self.pending_repo.find_by_token(registration_token)
        if pending is None:
            raise NotFoundError("Регистрация не найдена или истекла — начните заново")

        cooldown = settings.sms_code_resend_cooldown_seconds

        # Номер ещё не подтверждён — переотправлять нечего, отдаём тот же deep-link:
        # человеку надо вернуться в Telegram и нажать «Отправить мой номер»
        if pending.user_id is None:
            return messenger_service.build_deep_link(
                messenger_service.CHANNEL_TELEGRAM, pending.token
            ), cooldown

        user = await self.user_repo.get_by_id(pending.user_id)
        if user is None:
            raise NotFoundError("Регистрация не найдена или истекла — начните заново")
        if user.is_phone_verified:
            raise AlreadyExistsError("Пользователь уже подтверждён")

        remaining = await self._resend_cooldown_remaining(user.phone, PURPOSE_REGISTRATION)
        if remaining > 0:
            raise RateLimitError(f"Повторная отправка возможна через {remaining} сек.")

        link = await self.messenger_link_repo.find_by_user_and_channel(
            user.id, messenger_service.CHANNEL_TELEGRAM
        )
        if link is None:
            raise NotFoundError("Telegram не подключён — начните регистрацию заново")

        await self._deliver_code(
            user.id, user.phone, PURPOSE_REGISTRATION,
            messenger_service.CHANNEL_TELEGRAM, link.external_chat_id,
        )
        return None, cooldown
    
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
        contact_phone: str | None = None,
    ) -> User:
        if full_name is not None:
            user.full_name = full_name
        if email is not None:
            user.email = email
        if organization_name is not None:
            user.organization_name = organization_name
        if contact_phone is not None:
            user.contact_phone = contact_phone
        await self.session.commit()
        return user

    # Смена номера телефона живёт в app/services/phone_change_service.py и идёт
    # только через заявку с ручным одобрением админа. Здесь её быть не может:
    # _start_verification доставляет код по user_id — в мессенджер самого
    # заявителя, — поэтому подтвердить владение НОВЫМ номером этим механизмом
    # нельзя в принципе, пока SMS отключены.

    # Сколько секунд осталось до следующей попытки
    async def _resend_cooldown_remaining(self, phone: str, purpose: str) -> int:
        cooldown = settings.sms_code_resend_cooldown_seconds

        latest_code = await self.code_repo.find_latest(phone, purpose)
        if latest_code is None:
            return 0

        elapsed = (datetime.now(timezone.utc) - latest_code.created_at).total_seconds()
        remaining = cooldown - elapsed
        return int(remaining) if remaining > 0 else 0

    # Отправка кода в уже подтверждённую связку. Связки заводятся только при
    # регистрации, где номер доказан контактом из Telegram, — поэтому «нет связки»
    # означает «доставить некуда», а не «давайте заведём новую». Заводить её здесь
    # было бы дырой: сброс пароля открыт без авторизации, и токен привязки уходил
    # бы в ответе атакующему вместе с последующим кодом сброса.
    async def _start_verification(
        self, phone: str, purpose: str, channel: str, user_id: int,
    ) -> tuple[int, str | None]:
        cooldown = settings.sms_code_resend_cooldown_seconds

        link = await self.messenger_link_repo.find_by_user_and_channel(user_id, channel)
        if link is not None:
            await self._deliver_code(user_id, phone, purpose, channel, link.external_chat_id)

        return cooldown, None

    # Публичная обёртка над _deliver_code — вызывается из messenger_webhook_service.py
    # сразу после того, как номер подтверждён контактом из Telegram
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
