import logging

_log = logging.getLogger(__name__)


async def handle_telegram_update(payload: dict) -> None:
    """Обрабатывает входящий Telegram-апдейт. Нас интересует только первое
    сообщение '/start <token>', которое Telegram шлёт автоматически при открытии
    deep-link t.me/<bot>?start=<token> — это и есть подтверждение рукопожатия."""
    message = payload.get("message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if chat_id is None or not text.startswith("/start"):
        _log.info("Telegram webhook: не /start или нет chat_id, payload: %s", payload)
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        _log.info("Telegram webhook: /start без токена, payload: %s", payload)
        return

    await _complete_link("telegram", parts[1].strip(), str(chat_id))


async def handle_viber_event(payload: dict) -> None:
    """Обрабатывает входящее событие Viber. MVP-механизм — токен пользователь
    отправляет вручную (предзаполненный deep-link viber://pa?chatURI=...&text=<token>),
    поэтому интересует только event=message."""
    if payload.get("event") != "message":
        _log.info("Viber webhook: не message-событие, payload: %s", payload)
        return

    sender = payload.get("sender") or {}
    external_id = sender.get("id")
    message = payload.get("message") or {}
    token = (message.get("text") or "").strip()

    if not external_id or not token:
        _log.info("Viber webhook: не удалось извлечь sender.id/текст, payload: %s", payload)
        return

    await _complete_link("viber", token, str(external_id))


async def _complete_link(channel: str, token: str, external_chat_id: str) -> None:
    from app.database import AsyncSessionLocal
    from app.repositories.messenger import MessengerLinkRepository, MessengerLinkRequestRepository
    from app.services.auth_service import AuthService

    async with AsyncSessionLocal() as session:
        request_repo = MessengerLinkRequestRepository(session)
        request = await request_repo.find_by_token(token)
        if request is None or request.channel != channel:
            _log.info("Messenger webhook (%s): токен не найден/просрочен/не тот канал", channel)
            return

        link_repo = MessengerLinkRepository(session)
        await link_repo.upsert(request.user_id, channel, external_chat_id)
        await request_repo.mark_consumed(request)
        await session.commit()

        try:
            await AuthService(session).deliver_code_after_link(
                user_id=request.user_id, phone=request.phone, purpose=request.purpose,
                channel=channel, external_chat_id=external_chat_id,
            )
        except Exception:
            _log.exception(
                "Messenger webhook (%s): рукопожатие подтверждено, но не удалось отправить код", channel
            )
