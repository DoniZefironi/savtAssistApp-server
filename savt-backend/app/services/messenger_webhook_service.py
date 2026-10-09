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

    Одного /start недостаточно: код улетел бы тому, кто открыл ссылку, а введённый
    в форме номер не сверялся бы ни с чем — занять можно было бы любой чужой номер."""
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


_NO_REGISTRATION_TEXT = "Регистрация не найдена или истекла. Начните заново в приложении."


async def _fail_registration(session, pending, external_chat_id: str, reason: str, text: str) -> None:
    """Причина отказа сохраняется в заявке, чтобы приложение узнало о ней через
    GET /auth/register/status, а не ждало вечно код, которого не будет. Заявку
    при этом НЕ закрываем: по большинству причин кнопку можно нажать повторно и
    всё получится."""
    pending.failed_reason = reason
    await session.commit()
    await _reply(external_chat_id, text)


def _sender_phone_or_rejection(message: dict, contact: dict) -> tuple[str | None, tuple[str, str] | None]:
    """(номер, None) или (None, (причина, текст ответа)).

    Telegram позволяет отправить боту ЛЮБОЙ контакт из адресной книги. user_id
    есть только у контактов, которые сами являются пользователями Telegram, и
    совпадает с отправителем только для его собственной карточки. Без этой
    проверки достаточно отправить боту контакт жертвы — и её номер стал бы
    номером чужого аккаунта."""
    sender_id = (message.get("from") or {}).get("id")
    if contact.get("user_id") is None or sender_id is None or contact["user_id"] != sender_id:
        _log.warning(
            "Telegram webhook: прислан чужой контакт (from=%s, contact.user_id=%s)",
            sender_id, contact.get("user_id"),
        )
        return None, (
            "foreign_contact",
            "Это чужой контакт. Нажмите кнопку «Отправить мой номер» — "
            "переслать карточку другого человека нельзя.",
        )

    phone = _normalize_phone(contact.get("phone_number"))
    if phone is None:
        _log.warning("Telegram webhook: не удалось разобрать номер %s", contact.get("phone_number"))
        return None, ("bad_phone", "Не удалось разобрать ваш номер. Обратитесь в поддержку.")
    return phone, None


async def _account_conflict(session, external_chat_id: str, phone: str):
    """(незавершённый аккаунт на этот номер, None) или (None, (причина, текст)).

    Этот Telegram уже принадлежит подтверждённому аккаунту? Проверяем ДО поиска
    по телефону: номер аккаунта мог разойтись с номером Telegram — админ одобрил
    смену номера, либо человек сменил номер в самом Telegram. Тогда поиск по
    телефону никого не найдёт, и без этой проверки завёлся бы второй аккаунт на
    тот же чат: сброс пароля для обоих приходил бы в одно место, а различить их
    можно было бы только по тексту. Незавершённая регистрация не мешает — там
    аккаунт ещё не подтверждён, и человек просто проходит флоу заново."""
    from app.repositories.messenger import MessengerLinkRepository
    from app.repositories.user import UserRepository
    from app.services import messenger_service

    user_repo = UserRepository(session)
    existing_link = await MessengerLinkRepository(session).find_by_chat(
        messenger_service.CHANNEL_TELEGRAM, external_chat_id,
    )
    if existing_link is not None:
        linked = await user_repo.get_by_id(existing_link.user_id)
        if linked is not None and linked.is_phone_verified:
            _log.info("Telegram webhook: чат %s уже привязан к аккаунту %s", external_chat_id, linked.id)
            return None, (
                "telegram_already_linked",
                f"Этот Telegram уже привязан к аккаунту {linked.phone}. Войдите в "
                f"приложение по этому номеру, а если забыли пароль — воспользуйтесь "
                f"восстановлением.",
            )

    existing = await user_repo.find_by_phone(phone)
    if existing is not None and existing.is_phone_verified:
        _log.info("Telegram webhook: номер %s уже зарегистрирован", phone)
        return None, (
            "phone_already_registered",
            f"Номер {phone} уже зарегистрирован. Войдите в приложение по этому "
            f"номеру, а если забыли пароль — воспользуйтесь восстановлением.",
        )
    return existing, None


async def _user_for_registration(session, pending, phone: str, existing):
    """Аккаунт, на который оформляется заявка: незавершённая регистрация на тот
    же номер перезаписывается данными текущей заявки, иначе заводится новый."""
    from app.repositories.user import UserRepository

    if existing is not None:
        existing.hashed_password = pending.hashed_password
        existing.full_name = pending.full_name
        existing.user_type = pending.user_type
        existing.organization_name = pending.organization_name
        existing.contact_phone = pending.contact_phone
        return existing

    user = await UserRepository(session).create(
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
    return user


async def _handle_contact(external_chat_id: str, message: dict, contact: dict) -> None:
    """Шаг 2: пришёл контакт. Номер отсюда и становится номером аккаунта.

    Сверять его не с чем и не нужно: источник доверенный (Telegram проверил номер
    при регистрации аккаунта), а в форме приложения номер больше не спрашивается.
    Проверяем только, что контакт действительно принадлежит отправителю."""
    from app.database import AsyncSessionLocal
    from app.repositories.messenger import MessengerLinkRepository
    from app.repositories.pending_registration import PendingRegistrationRepository
    from app.services import messenger_service
    from app.services.auth_service import PURPOSE_REGISTRATION, AuthService

    async with AsyncSessionLocal() as session:
        pending = await PendingRegistrationRepository(session).find_by_chat(external_chat_id)
        if pending is None:
            _log.info("Telegram webhook: контакт без активной регистрации, чат %s", external_chat_id)
            await _reply(external_chat_id, _NO_REGISTRATION_TEXT)
            return

        phone, rejection = _sender_phone_or_rejection(message, contact)
        if rejection is None:
            existing, rejection = await _account_conflict(session, external_chat_id, phone)
        if rejection is not None:
            await _fail_registration(session, pending, external_chat_id, *rejection)
            return

        # Предыдущая попытка могла закончиться отказом — снимаем отметку
        pending.failed_reason = None
        user = await _user_for_registration(session, pending, phone, existing)

        pending.user_id = user.id
        await MessengerLinkRepository(session).upsert(user.id, messenger_service.CHANNEL_TELEGRAM, external_chat_id)
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
