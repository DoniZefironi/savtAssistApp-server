"""В лог не попадают секреты и личные данные: адреса исходящих запросов (в них токен
Telegram-бота и ключ вебхука Bitrix), тела входящих вебхуков, телефоны целиком."""
import logging
import re
from pathlib import Path

import pytest

from app.utils.log_safe import form_keys, mask_phone, telegram_update_summary

APP = Path(__file__).resolve().parent.parent / "app"


def test_httpx_does_not_log_request_urls():
    import app.main  # noqa: F401  (настройка логирования выполняется при импорте)

    # Смотрится уровень самого логгера, а не унаследованный: общий уровень приложения — INFO,
    # и без собственной настройки httpx писал бы адреса
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING


@pytest.mark.parametrize("phone,masked", [
    ("+375291234567", "+37529*****67"),
    ("80291234567", "802912***67"),
    ("12345", "*****"),
    (None, ""),
])
def test_phone_is_masked(phone, masked):
    assert mask_phone(phone) == masked


def test_form_is_logged_as_field_names_only():
    form = {"auth[application_token]": "SECRET-TOKEN", "data[FIELDS_AFTER][ID]": "77", "event": "ONTASKADD"}

    logged = form_keys(form)

    assert "SECRET-TOKEN" not in logged and "77" not in logged
    assert "auth[application_token]" in logged and "event" in logged
    assert form_keys({}) == "пусто"


def test_telegram_update_is_logged_without_personal_data():
    payload = {"update_id": 1, "message": {
        "message_id": 5, "date": 1, "chat": {"id": 9001, "first_name": "Иван", "username": "ivan_secret"},
        "from": {"id": 9001, "first_name": "Иван", "username": "ivan_secret"},
        "text": "мой пароль qwerty", "contact": {"phone_number": "+375291234567"},
    }}

    logged = telegram_update_summary(payload)

    for secret in ("Иван", "ivan_secret", "qwerty", "+375291234567"):
        assert secret not in logged
    assert "9001" in logged and "contact" in logged and "text" in logged and "17" in logged
    assert "callback_query" in telegram_update_summary({"update_id": 2, "callback_query": {"data": "x"}})
    assert telegram_update_summary(None) == "NoneType"


def test_webhook_handlers_do_not_log_raw_payloads():
    """Страж: тело входящего вебхука и номер телефона в лог идут только через log_safe."""
    offenders = []
    call = re.compile(r"(?:_log|logger)\.(?:debug|info|warning|error|exception)\((.*)\)\s*$")
    for name in ("bitrix_webhook_service.py", "messenger_webhook_service.py"):
        for number, line in enumerate((APP / "services" / name).read_text(encoding="utf-8").splitlines(), 1):
            match = call.search(line)
            if not match:
                continue
            args = match.group(1).split('"')[-1]          # то, что после строки формата
            for word in ("form", "payload", "phone", "message_text"):
                if re.search(rf"(?<![\w(.]){word}\b", args.replace("form_keys(form)", "")
                             .replace("telegram_update_summary(payload)", "")
                             .replace("mask_phone(phone)", "").replace("len(message_text", "")):
                    offenders.append(f"{name}:{number}: {line.strip()}")
    assert offenders == []
