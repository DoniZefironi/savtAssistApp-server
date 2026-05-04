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
    PasswordResetCompleteIn,
    PasswordResetStartIn,
    PasswordResetStartOut,
)
from app.services.auth_service import AuthService

# Все эндпоинты будут доступны по префиксу
router = APIRouter(prefix="/auth", tags=["auth"])

# Вспомогительная функция(аудит, подозрительная активность, блокировки)
def _client_info(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    return user_agent, ip

# Регистрация
@router.post("/register/start", response_model=RegisterStartOut)
async def register_start(
    payload: RegisterStartIn, # валидация
    session: AsyncSession = Depends(get_session), # создание сессии бд
):
    service = AuthService(session) # создание сервиса
    cooldown = await service.register_start(
        phone=payload.phone,
        password=payload.password,
        full_name=payload.full_name,
    )
    return RegisterStartOut(resend_after_seconds=cooldown)

# Подтверждение кода
@router.post("/register/complete", response_model=TokenPairOut, status_code=status.HTTP_201_CREATED)
async def register_complete(
    payload: RegisterCompleteIn,
    request: Request, # для получения IP
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

# Переотправка кода
@router.post("/register/resend", response_model=RegisterStartOut)
async def register_resend(
    payload: RegisterStartIn,  
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    cooldown = await service.register_resend_code(payload.phone)
    return RegisterStartOut(resend_after_seconds=cooldown)

# Вход
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

# Обновление токенов
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

# Выход из аккаунта
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutIn,
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    await service.logout(payload.refresh_token)

# Профиль текущего пользователя
@router.get("/me", response_model=UserMeOut)
async def me(
    user: User = Depends(get_current_user), # проверка токена
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

# Восстановление пароля: запрос кода
@router.post("/password-reset/start", response_model=PasswordResetStartOut)
async def password_reset_start(
    payload: PasswordResetStartIn,
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    cooldown = await service.password_reset_start(payload.phone)
    return PasswordResetStartOut(resend_after_seconds=cooldown)

# Восстановление пароля: новый пароль
@router.post("/password-reset/complete", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset_complete(
    payload: PasswordResetCompleteIn,
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    await service.password_reset_complete(
        phone=payload.phone,
        code=payload.code,
        new_password=payload.new_password,
    )
