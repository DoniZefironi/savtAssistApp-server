"""Что можно писать в лог вместо самих данных. Лог читают шире, чем базу (docker logs,
выгрузки для разбора сбоев), поэтому секретов и личных данных в нём быть не должно:
телефон маскируется, от тела запроса остаётся только его форма."""


def mask_phone(phone) -> str:
    """+375291234567 -> +37529*****67. По такому виду запись в логе можно сопоставить с
    обращением человека, но номер из него не восстановить."""
    text = str(phone or "")
    if len(text) < 8:
        return "*" * len(text)
    return f"{text[:6]}{'*' * (len(text) - 8)}{text[-2:]}"


def form_keys(form) -> str:
    """Имена полей формы без значений: среди значений вебхука Bitrix есть application_token."""
    try:
        return ", ".join(sorted(str(key) for key in form.keys())) or "пусто"
    except AttributeError:
        return type(form).__name__


def telegram_update_summary(payload) -> str:
    """Вид сообщения Telegram без текста, имени и username отправителя."""
    if not isinstance(payload, dict):
        return type(payload).__name__
    message = payload.get("message")
    if not isinstance(message, dict):
        return "update: " + (", ".join(sorted(k for k in payload if k != "update_id")) or "пусто")
    chat_id = (message.get("chat") or {}).get("id")
    kinds = sorted(k for k in message if k not in ("message_id", "from", "chat", "date"))
    text = message.get("text")
    length = f", длина текста {len(text)}" if isinstance(text, str) else ""
    return f"чат {chat_id}, поля сообщения: {', '.join(kinds) or 'нет'}{length}"
