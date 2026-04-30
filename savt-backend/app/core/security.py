import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ============================================================
# Пароли
# ============================================================

def hash_password(plain_password: str) -> str:
    """Хешируем пароль bcrypt-ом перед сохранением в БД."""
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверяем пароль при логине."""
    return _pwd_context.verify(plain_password, hashed_password)


# ============================================================
# Токены — для refresh-токенов и SMS-кодов
# ============================================================

def generate_refresh_token() -> str:
    """
    Генерируем криптографически безопасную случайную строку.
    Это значение целиком отдаётся клиенту, в БД хранится только её хеш.
    """
    return secrets.token_urlsafe(48)  # ~64 символа в base64url


def hash_token(token: str) -> str:
    """
    Хешируем refresh-токен или SMS-код через SHA-256 перед сохранением.
    Bcrypt тут не нужен — токены длинные и случайные, перебор невозможен.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_sms_code() -> str:
    """
    Генерируем 6-значный SMS-код. secrets — для криптостойкости (random использовать нельзя).
    """
    return f"{secrets.randbelow(1_000_000):06d}"


# ============================================================
# JWT (access-токены)
# ============================================================

_JWT_ALGORITHM = "HS256"


def create_access_token(user_id: int, role: str) -> str:
    """
    Создаём JWT access-токен. Время жизни короткое (по умолчанию 30 минут).
    В payload кладём id пользователя и роль — этого достаточно для авторизации
    без обращения к БД на каждом запросе.
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Проверяем и декодируем JWT.
    Бросает jwt.ExpiredSignatureError если истёк, jwt.InvalidTokenError если невалидный.
    """
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])