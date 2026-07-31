import logging

import phonenumbers

_log = logging.getLogger(__name__)


def _normalize_phone(raw: str | None) -> str | None:
    """Приводит номер к E.164. Telegram отдаёт его без ведущего '+'
    ("375291234567"), а phonenumbers без плюса и без региона не распарсит."""
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate.startswith("+"):
        candidate = "+" + candidate
    try:
        parsed = phonenumbers.parse(candidate, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


async def handle_telegram_update(payload: dict) -> None:
    """Входящий апдейт Telegram. Подтверждение номера идёт в два шага:

    1) '/start <token>' из deep-link — запоминаем чат и просим поделиться номером;
    2) message.contact — Telegram сам ручается за номер, сверяем с заявленным.

    Раньше хватало одного /start: код улетал тому, кто открыл ссылку, а введённый
    в форме номер не сверялся ни с чем — занять можно было любой чужой номер."""
    message = payload.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        _log.info("Telegram webhook: нет chat_id, payload: %s", payload)
        return

    contact = message.get("contact")
    if contact:
        await _handle_contact(str(chat_id), message, contact)
        return

    text = (message.get("text") or "").strip()
    if not text.startswith("/start"):
        _log.info("Telegram webhook: не /start и не контакт, payload: %s", payload)
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        _log.info("Telegram webhook: /start без токена, payload: %s", payload)
        return

    await _begin_link(parts[1].strip(), str(chat_id))


async def _begin_link(token: str, external_chat_id: str) -> None:
    """Шаг 1: приняли токен. Пользователя ещё нет — номер неизвестен, просим контакт."""
    from app.database import AsyncSessionLocal
    from app.repositories.pending_registration import PendingRegistrationRepository
    from app.services import messenger_service

    async with AsyncSessionLocal() as session:
        pending = await PendingRegistrationRepository(session).find_by_token(token)
        if pending is None:
            _log.info("Telegram webhook: регистрация не найдена или истекла")
            await _reply(external_chat_id, "Регистрация не найдена или истекла. Начните заново в приложении.")
            return

        pending.external_chat_id = external_chat_id
        await session.commit()

        try:
            await messenger_service.send_contact_request(
                messenger_service.CHANNEL_TELEGRAM, external_chat_id
            )
        except messenger_service.MessengerSendError:
            _log.exception("Telegram webhook: не удалось запросить контакт")


async def _handle_contact(external_chat_id: str, message: dict, contact: dict) -> None:
    """Шаг 2: пришёл контакт. Номер отсюда и становится номером аккаунта.

    Сверять его не с чем и не нужно: источник доверенный (Telegram проверил номер
    при регистрации аккаунта), а в форме приложения номер больше не спрашивается.
    Проверяем только, что контакт действительно принадлежит отправителю."""
    from app.database import AsyncSessionLocal
    from app.repositories.messenger import MessengerLinkRepository
    from app.repositories.pending_registration import PendingRegistrationRepository
    from app.repositories.user import UserRepository
    from app.services import messenger_service
    from app.services.auth_service import PURPOSE_REGISTRATION, AuthService

    async with AsyncSessionLocal() as session:
        pending_repo = PendingRegistrationRepository(session)
        pending = await pending_repo.find_by_chat(external_chat_id)
        if pending is None:
            _log.info("Telegram webhook: контакт без активной регистрации, чат %s", external_chat_id)
            await _reply(
                external_chat_id,
                "Регистрация не найдена или истекла. Начните заново в приложении.",
            )
            return

        # Telegram позволяет отправить боту ЛЮБОЙ контакт из адресной книги.
        # user_id есть только у контактов, которые сами являются пользователями
        # Telegram, и совпадает с отправителем только для его собственной карточки.
        # Без этой проверки достаточно отправить боту контакт жертвы — и её номер
        # стал бы номером чужого аккаунта.
        sender_id = (message.get("from") or {}).get("id")
        if contact.get("user_id") is None or sender_id is None or contact["user_id"] != sender_id:
            _log.warning(
                "Telegram webhook: прислан чужой контакт (from=%s, contact.user_id=%s)",
                sender_id, contact.get("user_id"),
            )
            await _reply(
                external_chat_id,
                "Это чужой контакт. Нажмите кнопку «Отправить мой номер» — "
                "переслать карточку другого человека нельзя.",
            )
            return

        phone = _normalize_phone(contact.get("phone_number"))
        if phone is None:
            _log.warning("Telegram webhook: не удалось разобрать номер %s", contact.get("phone_number"))
            await _reply(external_chat_id, "Не удалось разобрать ваш номер. Обратитесь в поддержку.")
            return

        user_repo = UserRepository(session)
        existing = await user_repo.find_by_phone(phone)
        if existing is not None and existing.is_phone_verified:
            _log.info("Telegram webhook: номер %s уже зарегистрирован", phone)
            await _reply(
                external_chat_id,
                f"Номер {phone} уже зарегистрирован. Войдите в приложение по этому "
                f"номеру, а если забыли пароль — воспользуйтесь восстановлением.",
            )
            return

        if existing is not None:
            # Незавершённая регистрация на тот же номер — перезаписываем данными
            # текущей заявки (так же вёл себя register_start до этой переделки)
            existing.hashed_password = pending.hashed_password
            existing.full_name = pending.full_name
            existing.user_type = pending.user_type
            existing.organization_name = pending.organization_name
            existing.contact_phone = pending.contact_phone
            user = existing
        else:
            user = await user_repo.create(
                phone=phone,
                full_name=pending.full_name,
                hashed_password=pending.hashed_password,
                role_id=1,
                is_phone_verified=False,
                is_active=True,
                user_type=pending.user_type,
                organization_name=pending.organization_name,
                contact_phone=pending.contact_phone,
            )
            await session.flush()

        pending.user_id = user.id
        await MessengerLinkRepository(session).upsert(
            user.id, messenger_service.CHANNEL_TELEGRAM, external_chat_id
        )
        await session.commit()

        try:
            await AuthService(session).deliver_code_after_link(
                user_id=user.id, phone=phone, purpose=PURPOSE_REGISTRATION,
                channel=messenger_service.CHANNEL_TELEGRAM, external_chat_id=external_chat_id,
            )
        except Exception:
            _log.exception("Telegram webhook: номер подтверждён, но код отправить не удалось")


async def _reply(external_chat_id: str, text: str) -> None:
    from app.services import messenger_service
    try:
        await messenger_service.send_plain(
            messenger_service.CHANNEL_TELEGRAM, external_chat_id, text
        )
    except messenger_service.MessengerSendError:
        _log.exception("Telegram webhook: не удалось отправить ответ в чат %s", external_chat_id)
