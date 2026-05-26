from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.auth import (
    AdminLoginIn,
    ChangePhoneCompleteIn,
    ChangePhoneStartIn,
    LoginIn,
    LogoutIn,
    PasswordChange,
    PasswordResetCompleteIn,
    PasswordResetStartIn,
    PasswordResetStartOut,
    RefreshIn,
    RegisterCompleteIn,
    RegisterStartIn,
    RegisterStartOut,
    ResendCodeIn,
    TokenPairOut,
    UpdateProfileIn,
    UserMeOut,
)
from app.services.auth_service import AuthService
from app.models.role import Role

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
        user_type=payload.user_type,
        organization_name=payload.organization_name
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
    payload: ResendCodeIn,  
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    cooldown = await service.register_resend_code(payload.phone)
    return RegisterStartOut(resend_after_seconds=cooldown)

# Вход для администратора / оператора
@router.post("/admin-login", response_model=TokenPairOut)
async def admin_login(
    payload: AdminLoginIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user_agent, ip = _client_info(request)
    service = AuthService(session)
    access, refresh = await service.admin_login(
        login=payload.login,
        password=payload.password,
        user_agent=user_agent,
        ip_address=ip,
    )
    return TokenPairOut(access_token=access, refresh_token=refresh)

# Вход пользователя
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
    role = await session.get(Role, user.role_id)
    return UserMeOut(
        id=user.id,
        phone=user.phone,
        full_name=user.full_name,
        role=role.name if role else "user",
        is_phone_verified=user.is_phone_verified,
        is_verified=user.is_verified,
        email=user.email,
        user_type=user.user_type,
        organization_name=user.organization_name
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
        new_password_confirm=payload.new_password_confirm
    )
    
# Смена пароля
@router.post('/password-change', status_code=status.HTTP_200_OK)
async def change_password(
    payload: PasswordChange,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    await service.change_password(
        user=current_user,
        old_password=payload.password,
        new_password=payload.new_password,
        new_password_confirm=payload.new_password_confirm,
    )
    return {"message": "Пароль успешно сохранён"}

# Редактирование профиля
@router.patch("/me", response_model=UserMeOut)
async def update_profile(
    payload: UpdateProfileIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    user = await service.update_profile(
        user=current_user,
        full_name=payload.full_name,
        email=payload.email,
        organization_name=payload.organization_name,
    )
    role = await session.get(Role, user.role_id)
    return UserMeOut(
        id=user.id,
        phone=user.phone,
        full_name=user.full_name,
        role=role.name if role else "user",
        is_phone_verified=user.is_phone_verified,
        is_verified=user.is_verified,
        email=user.email,
        user_type=user.user_type,
        organization_name=user.organization_name,
    )

# Удаление аккаунта
@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await AuthService(session).delete_account(current_user)

# Смена номера телефона — запрос кода
@router.post("/change-phone/start", response_model=RegisterStartOut)
async def change_phone_start(
    payload: ChangePhoneStartIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    cooldown = await service.change_phone_start(current_user, payload.new_phone)
    return RegisterStartOut(resend_after_seconds=cooldown)

# Смена номера телефона — подтверждение
@router.post("/change-phone/complete", status_code=status.HTTP_204_NO_CONTENT)
async def change_phone_complete(
    payload: ChangePhoneCompleteIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    await service.change_phone_complete(current_user, payload.new_phone, payload.code)