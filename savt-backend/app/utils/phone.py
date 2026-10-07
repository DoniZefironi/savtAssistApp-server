import re

from app.schemas.auth import _normalize_phone

# Телефон есть, но разобрать его нельзя
INVALID_PHONE = False


def normalize_loose_phone(raw: str | None) -> str | None | bool:
    """E.164; None — телефона нет; INVALID_PHONE (False) — телефон есть, но
    разобрать его нельзя. Понимает "+375 (29) 111-22-33", "375291112233",
    "80291112233", "291112233" — так номера вводят люди и так они лежат в
    Bitrix, в отличие от строгого _normalize_phone, которому нужен "+"."""
    if not raw or not raw.strip():
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("80"):
        digits = "375" + digits[2:]
    elif len(digits) == 9:
        digits = "375" + digits
    try:
        return _normalize_phone("+" + digits)
    except ValueError:
        return INVALID_PHONE
