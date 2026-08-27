import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings

# настройка хеширования паролей через bcrypt 
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# хеширование пароля
def hash_password(plain_password: str) -> str:
    # passlib сам генерирует случайную соль, хеширует и возращает одну строку
    return _pwd_context.hash(plain_password)

# верификация пароля
def verify_password(plain_password: str, hashed_password: str) -> bool:
    # passlib достаёт соль и параметры прям из хэша, хэширует пароль с теми же паратметрами и сравнивает результат с хэшем, возравщает бул
    return _pwd_context.verify(plain_password, hashed_password)

# генерация рефреш токена
def generate_refresh_token() -> str:
    # 48 случайных байт и кодировка их в base64 
    return secrets.token_urlsafe(48) 

# хеширование токена
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# генерация смс кода
def generate_sms_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"

# алгоритм подписи для jwt
_JWT_ALGORITHM = "HS256"

# создание аксесс токена
def create_access_token(user_id: int, role: str) -> str:
    # текущее время
    now = datetime.now(timezone.utc)
    # тело токена
    payload: dict[str, Any] = {
        # кому выдан токен
        "sub": str(user_id),
        # роль
        "role": role,
        # тип токена
        "type": "access",
        # время выпуска токена
        "iat": int(now.timestamp()),
        # время истечения
        # settings.jwt_access_token_ttl_minutes - TTL берется из конфига
        "exp": int((now + timedelta(minutes=settings.jwt_access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)

# проверить и распарсить аксес-токен обратно в данные
def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])

# создание гостевого токена
def create_guest_token() -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": "guest",
        "role": "guest",
        "type": "guest",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)