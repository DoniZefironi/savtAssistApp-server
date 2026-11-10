import asyncio
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
    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        # Значение не является хешем (например, строка-заглушка у удалённых учёток) —
        # пароль не подходит, а не ошибка сервера
        return False

# bcrypt считается около 0,2 с и целиком нагружает процессор: вызванный прямо в обработчике
# запроса, он останавливает цикл событий, а с ним и весь API. В запросах используются эти
# версии, синхронные остаются для консольных команд и системных пользователей при старте
async def hash_password_async(plain_password: str) -> str:
    return await asyncio.to_thread(hash_password, plain_password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)

_dummy_hash: str | None = None


async def burn_password_check(plain_password: str) -> None:
    """Делает ту же работу, что проверка пароля настоящей учётки. Вызывается при входе,
    когда пользователя с таким логином нет: иначе ответ приходит на ~0,2 с быстрее, и по
    времени ответа можно узнать, какие номера зарегистрированы."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await hash_password_async(secrets.token_urlsafe(16))
    await verify_password_async(plain_password, _dummy_hash)

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