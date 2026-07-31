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
    """Шаг 1: приняли токен. Связку НЕ создаём и код НЕ шлём — сначала номер."""
    from app.database import AsyncSessionLocal
    from app.repositories.messenger import MessengerLinkRequestRepository
    from app.services import messenger_service

    async with AsyncSessionLocal() as session:
        request_repo = MessengerLinkRequestRepository(session)
        request = await request_repo.find_by_token(token)
        if request is None or request.channel != messenger_service.CHANNEL_TELEGRAM:
            _log.info("Telegram webhook: токен не найден/просрочен/не тот канал")
            return

        request.external_chat_id = external_chat_id
        await session.commit()

        try:
            await messenger_service.send_contact_request(
                request.channel, external_chat_id, request.phone
            )
        except messenger_service.MessengerSendError:
            _log.exception("Telegram webhook: не удалось запросить контакт")


async def _handle_contact(external_chat_id: str, message: dict, contact: dict) -> None:
    """Шаг 2: пришёл контакт. Три проверки, и все три обязательны."""
    from app.database import AsyncSessionLocal
    from app.repositories.messenger import MessengerLinkRepository, MessengerLinkRequestRepository
    from app.services import messenger_service
    from app.services.auth_service import AuthService

    async with AsyncSessionLocal() as session:
        request_repo = MessengerLinkRequestRepository(session)
        request = await request_repo.find_pending_by_chat(external_chat_id)
        if request is None:
            _log.info("Telegram webhook: контакт без активной заявки, чат %s", external_chat_id)
            await _reply(
                external_chat_id,
                "Заявка не найдена или истекла. Запросите код в приложении заново.",
            )
            return

        # (1) Telegram позволяет отправить боту ЛЮБОЙ контакт из адресной книги.
        # user_id есть только у контактов, которые сами являются пользователями
        # Telegram, и совпадает с отправителем только для его собственной карточки.
        # Без этой проверки достаточно отправить боту контакт жертвы.
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

        # (2) Номер должен совпасть с заявленным при регистрации
        shared_phone = _normalize_phone(contact.get("phone_number"))
        if shared_phone is None or shared_phone != request.phone:
            _log.warning(
                "Telegram webhook: номер не совпал (ожидали %s, прислали %s)",
                request.phone, shared_phone,
            )
            await _reply(
                external_chat_id,
                f"Номер вашего Telegram не совпадает с указанным при регистрации "
                f"({request.phone}). Начните заново в приложении, указав номер "
                f"этого Telegram-аккаунта.",
            )
            return

        # (3) Заявка жива и не использована — это уже проверено find_pending_by_chat

        link_repo = MessengerLinkRepository(session)
        await link_repo.upsert(request.user_id, request.channel, external_chat_id)
        await request_repo.mark_consumed(request)
        await session.commit()

        try:
            await AuthService(session).deliver_code_after_link(
                user_id=request.user_id, phone=request.phone, purpose=request.purpose,
                channel=request.channel, external_chat_id=external_chat_id,
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
