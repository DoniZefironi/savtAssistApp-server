from urllib.parse import unquote


def clean_code(raw: str) -> str:
    """Код из QR/ссылки в том виде, в каком он лежит в БД: ссылка может прийти
    с процент-кодированием ("%3D%3D" вместо "==" в конце Fernet-кода проекта),
    с лишним "/" на конце, с параметрами запроса или якорем."""
    code = raw.strip().split("#", 1)[0].split("?", 1)[0]
    return unquote(code).strip().rstrip("/")
