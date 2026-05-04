import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Хешируем пароль bcrypt-ом перед сохранением в БД.
def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)

# Проверяем пароль при логине.
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)

# Генерируем криптографически безопасную случайную строку.
# Это значение целиком отдаётся клиенту, в БД хранится только её хеш.
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48) 

# Хешируем refresh-токен или SMS-код через SHA-256 перед сохранением.
# Bcrypt тут не нужен — токены длинные и случайные, перебор невозможен.
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# Генерируем 6-значный SMS-код. secrets — для криптостойкости.
def generate_sms_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


_JWT_ALGORITHM = "HS256"

# Создаём JWT access-токен. Время жизни короткое (по умолчанию 30 минут).
# В payload кладём id пользователя и роль — этого достаточно для авторизации
# без обращения к БД на каждом запросе.
def create_access_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)

# Проверяем и декодируем JWT.
# Бросает jwt.ExpiredSignatureError если истёк, jwt.InvalidTokenError если невалидный.
def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])