from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import (
    LoginIn,
    LogoutIn,
    RefreshIn,
    RegisterCompleteIn,
    RegisterStartIn,
    RegisterStartOut,
    TokenPairOut,
    UserMeOut,
)
from app.services.auth_service import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])


def _client_info(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    return user_agent, ip


@router.post("/register/start", response_model=RegisterStartOut)
async def register_start(
    payload: RegisterStartIn,
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    cooldown = await service.register_start(
        phone=payload.phone,
        password=payload.password,
        full_name=payload.full_name,
    )
    return RegisterStartOut(resend_after_seconds=cooldown)


@router.post("/register/complete", response_model=TokenPairOut, status_code=status.HTTP_201_CREATED)
async def register_complete(
    payload: RegisterCompleteIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_agent, ip = _client_info(request)
    service = AuthService(session)
    access, refresh = await service.register_complete(
        phone=payload.phone,
        code=payload.code,
        user_agent=user_agent,
        ip_address=ip,
    )
    return TokenPairOut(access_token=access, refresh_token=refresh)


@router.post("/login", response_model=TokenPairOut)
async def login(
    payload: LoginIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_agent, ip = _client_info(request)
    service = AuthService(session)
    access, refresh = await service.login(
        phone=payload.phone,
        password=payload.password,
        user_agent=user_agent,
        ip_address=ip,
    )
    return TokenPairOut(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenPairOut)
async def refresh(
    payload: RefreshIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_agent, ip = _client_info(request)
    service = AuthService(session)
    access, new_refresh = await service.refresh_tokens(
        refresh_token=payload.refresh_token,
        user_agent=user_agent,
        ip_address=ip,
    )
    return TokenPairOut(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutIn,
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    await service.logout(payload.refresh_token)


@router.get("/me", response_model=UserMeOut)
async def me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.models.roles import Role
    role = await session.get(Role, user.role_id)
    return UserMeOut(
        id=user.id,
        phone=user.phone,
        full_name=user.full_name,
        role=role.name if role else "user",
        is_phone_verified=user.is_phone_verified,
    )