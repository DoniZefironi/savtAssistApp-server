import hashlib
import hmac
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15)
    return _client


class MessengerSendError(Exception):
    """Не удалось доставить сообщение (бот заблокирован пользователем, чат недоступен и т.п.)."""


def build_deep_link(channel: str, token: str) -> str:
    if channel == "telegram":
        return f"https://t.me/{settings.telegram_bot_username}?start={token}"
    if channel == "viber":
        return f"viber://pa?chatURI={settings.viber_bot_uri}&text={token}"
    raise ValueError(f"Неизвестный канал доставки: {channel}")


async def send_verification_code(channel: str, external_chat_id: str, code: str) -> None:
    text = f"Код подтверждения SAVT Assist: {code}"
    if channel == "telegram":
        await _send_telegram(external_chat_id, text)
    elif channel == "viber":
        await _send_viber(external_chat_id, text)
    else:
        raise ValueError(f"Неизвестный канал доставки: {channel}")


async def _send_telegram(chat_id: str, text: str) -> None:
    # Токен бота не задан — dev-режим, просто логируем (аналог MockSmsProvider)
    if not settings.telegram_bot_token:
        logger.info(f"[MOCK TELEGRAM] chat_id={chat_id}: {text}")
        print(f"\n>>> Telegram {chat_id}: {text} <<<\n", flush=True)
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        resp = await _get_client().post(url, json={"chat_id": chat_id, "text": text})
    except httpx.RequestError as e:
        raise MessengerSendError(f"Telegram сетевая ошибка: {e}") from e

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if not resp.is_success or not data.get("ok", False):
        raise MessengerSendError(f"Telegram sendMessage {resp.status_code}: {resp.text}")


async def _send_viber(receiver_id: str, text: str) -> None:
    if not settings.viber_bot_token:
        logger.info(f"[MOCK VIBER] receiver={receiver_id}: {text}")
        print(f"\n>>> Viber {receiver_id}: {text} <<<\n", flush=True)
        return

    url = "https://chatapi.viber.com/pa/send_message"
    try:
        resp = await _get_client().post(
            url,
            headers={"X-Viber-Auth-Token": settings.viber_bot_token},
            json={
                "receiver": receiver_id,
                "min_api_version": 1,
                "sender": {"name": "SAVT Assist"},
                "type": "text",
                "text": text,
            },
        )
    except httpx.RequestError as e:
        raise MessengerSendError(f"Viber сетевая ошибка: {e}") from e

    data = resp.json() if resp.is_success else {}
    # status == 0 — успех; ненулевые коды означают, что пользователь не подписан/заблокировал бота
    # (точные значения кодов требуют проверки на реальном аккаунте Viber)
    if not resp.is_success or data.get("status", -1) != 0:
        raise MessengerSendError(f"Viber send_message {resp.status_code}: {resp.text}")


def verify_telegram_secret(header_value: str | None) -> bool:
    return bool(settings.telegram_webhook_secret) and hmac.compare_digest(
        header_value or "", settings.telegram_webhook_secret
    )


def verify_viber_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not settings.viber_bot_token or not signature_header:
        return False
    expected = hmac.new(settings.viber_bot_token.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
