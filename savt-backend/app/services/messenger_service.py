import hmac
import html
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

# Единственный канал доставки. Viber удалён: подтвердить владение номером через
# него нечем — аналога Telegram request_contact у Viber нет, а без него код
# доказывает лишь то, что человек открыл ссылку, но не то, что номер его.
CHANNEL_TELEGRAM = "telegram"


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15)
    return _client


class MessengerSendError(Exception):
    """Не удалось доставить сообщение (бот заблокирован пользователем, чат недоступен и т.п.)."""


def build_deep_link(channel: str, token: str) -> str:
    if channel == CHANNEL_TELEGRAM:
        return f"https://t.me/{settings.telegram_bot_username}?start={token}"
    raise ValueError(f"Неизвестный канал доставки: {channel}")


async def send_verification_code(channel: str, external_chat_id: str, code: str) -> None:
    """Шлёт код двумя сообщениями: пояснение отдельно, сам код отдельно.

    Одним сообщением код приходилось выделять из строки текста — на телефоне это
    неудобно. Отдельным сообщением он копируется долгим тапом целиком."""
    if channel != CHANNEL_TELEGRAM:
        raise ValueError(f"Неизвестный канал доставки: {channel}")
    await _send_telegram(
        external_chat_id,
        "Вот код подтверждения SAVT Assist:",
        # Заодно убираем кнопку «Отправить мой номер» — она уже отработала
        reply_markup={"remove_keyboard": True},
    )
    # Моноширинный блок: в Telegram по нему копируется в один тап.
    # HTML, а не MarkdownV2 — экранировать нужно всего три символа, а не полтора
    # десятка, так что разметка не сломается, даже если формат кода поменяется.
    await _send_telegram(
        external_chat_id, f"<code>{html.escape(code)}</code>", parse_mode="HTML"
    )


# Просит поделиться номером. Telegram подставит в контакт номер, который сам
# проверил при регистрации аккаунта, — ввести произвольный нельзя. Этот номер и
# станет номером аккаунта, поэтому в форме приложения его не спрашивают.
async def send_contact_request(channel: str, external_chat_id: str) -> None:
    if channel != CHANNEL_TELEGRAM:
        raise ValueError(f"Неизвестный канал доставки: {channel}")
    await _send_telegram(
        external_chat_id,
        "Остался один шаг — нажмите кнопку ниже, чтобы подтвердить номер телефона.\n\n"
        "Этот номер станет вашим логином в SAVT Assist. Telegram отправит его сам, "
        "вводить вручную не нужно.",
        reply_markup={
            "keyboard": [[{"text": "Отправить мой номер", "request_contact": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        },
    )


async def send_plain(channel: str, external_chat_id: str, text: str) -> None:
    if channel != CHANNEL_TELEGRAM:
        raise ValueError(f"Неизвестный канал доставки: {channel}")
    # remove_keyboard убирает кнопку "Отправить мой номер" после того, как она
    # отработала или больше не нужна — иначе она висит у пользователя навсегда
    await _send_telegram(external_chat_id, text, reply_markup={"remove_keyboard": True})


async def _send_telegram(
    chat_id: str,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
) -> None:
    # Токен бота не задан — dev-режим, просто логируем (аналог MockSmsProvider)
    if not settings.telegram_bot_token:
        logger.info(f"[MOCK TELEGRAM] chat_id={chat_id}: {text}")
        print(f"\n>>> Telegram {chat_id}: {text} <<<\n", flush=True)
        return

    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    # Ставится точечно, только там, где разметка действительно нужна: с включённым
    # parse_mode Telegram отвергнет любое сообщение с неэкранированным спецсимволом
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        resp = await _get_client().post(url, json=payload)
    except httpx.RequestError as e:
        raise MessengerSendError(f"Telegram сетевая ошибка: {e}") from e

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if not resp.is_success or not data.get("ok", False):
        raise MessengerSendError(f"Telegram sendMessage {resp.status_code}: {resp.text}")


def verify_telegram_secret(header_value: str | None) -> bool:
    return bool(settings.telegram_webhook_secret) and hmac.compare_digest(
        header_value or "", settings.telegram_webhook_secret
    )
