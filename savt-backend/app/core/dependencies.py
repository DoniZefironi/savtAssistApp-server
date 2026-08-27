from typing import AsyncGenerator
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.core.constants import RoleName
from app.core.security import decode_access_token
from app.database import AsyncSessionLocal
from app.models.user import User
from app.repositories.user import UserRepository
from app.models.role import Role

# извлечение Beaere-токен из заголовка авторизации
_security = HTTPBearer(auto_error=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def _decode_or_401(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не авторизован",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Токен истёк",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
    session: AsyncSession = Depends(get_session),
) -> User:
    payload = _decode_or_401(credentials)

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Неверный тип токена")

    user_id = int(payload["sub"])
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(user_id)

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь недоступен")

    return user


async def get_current_user_or_guest(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
    session: AsyncSession = Depends(get_session),
) -> User | None:
    payload = _decode_or_401(credentials)

    if payload.get("type") == "guest":
        return None

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Неверный тип токена")

    user_id = int(payload["sub"])
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь недоступен")
    return user


async def get_role_from_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> str:
    if credentials is None:
        return "unknown"
    try:
        payload = decode_access_token(credentials.credentials)
        return payload.get("role", "user")
    except Exception:
        return "unknown"


_ROLE_HIERARCHY: dict[str, set[str]] = {
    "user":       {"user", "operator", "admin", "superadmin"},
    "operator":   {"operator", "admin", "superadmin"},
    "admin":      {"admin", "superadmin"},
    "superadmin": {"superadmin"},
}


def require_role(*allowed_roles: RoleName):
    expanded = set()
    for r in allowed_roles:
        expanded.update(_ROLE_HIERARCHY.get(r.value, {r.value}))

    async def checker(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> User:
        role = await session.get(Role, user.role_id)
        if role is None or role.name not in expanded:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    return checker