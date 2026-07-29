import binascii

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

# Разделитель между номером проекта и "пеппером" в открытом тексте перед шифрованием
_SEPARATOR = "|"


class ProjectCodeError(Exception):
    """Ключ шифрования не настроен — encrypt/decrypt вызывать нельзя."""


def _get_fernet() -> Fernet:
    if not settings.bitrix_project_code_key:
        raise ProjectCodeError("BITRIX_PROJECT_CODE_KEY не настроен")
    return Fernet(settings.bitrix_project_code_key.encode("utf-8"))


def encrypt_project_code(production_number: str) -> str:
    """Шифрует номер проекта из Bitrix (например '26_138') в значение unique_code.
    Обратимо — см. decrypt_project_code. Каждый вызов даёт разный результат
    (Fernet использует случайный IV), но расшифровывается всегда в один и тот же номер."""
    plaintext = f"{production_number}{_SEPARATOR}{settings.bitrix_project_code_pepper}"
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_project_code(code: str) -> str | None:
    """Расшифровывает unique_code обратно в номер проекта. None — если код
    не расшифровался (неверный/чужой ключ) или "пеппер" не совпал (испорченные данные)."""
    try:
        plaintext = _get_fernet().decrypt(code.encode("utf-8")).decode("utf-8")
    except (InvalidToken, binascii.Error, ValueError):
        return None
    production_number, sep, pepper = plaintext.rpartition(_SEPARATOR)
    if not sep or pepper != settings.bitrix_project_code_pepper or not production_number:
        return None
    return production_number
