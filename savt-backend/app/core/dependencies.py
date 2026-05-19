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


_security = HTTPBearer(auto_error=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
    session: AsyncSession = Depends(get_session),
) -> User:

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не авторизован",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
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

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Неверный тип токена")

    user_id = int(payload["sub"])
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(user_id)

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь недоступен")

    return user


async def get_role_from_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> str:
    """Читает роль из JWT без запроса в БД. Используется для audit logging."""
    if credentials is None:
        return "unknown"
    try:
        payload = decode_access_token(credentials.credentials)
        return payload.get("role", "user")
    except Exception:
        return "unknown"


def require_role(*allowed_roles: RoleName):

    async def checker(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> User:
        role = await session.get(Role, user.role_id)
        if role is None or role.name not in {r.value for r in allowed_roles}:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    return checker