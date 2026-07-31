from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.auth import (
    AdminLoginIn,
    LoginIn,
    LogoutIn,
    PasswordChange,
    PasswordResetCompleteIn,
    PasswordResetStartIn,
    PasswordResetStartOut,
    PhoneChangeRequestCreateIn,
    PhoneChangeRequestOut,
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
from app.services.phone_change_service import PhoneChangeService
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
@limiter.limit("5/minute")
async def register_start(
    request: Request,
    payload: RegisterStartIn,
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session) # создание сервиса
    cooldown, deep_link = await service.register_start(
        phone=payload.phone,
        password=payload.password,
        full_name=payload.full_name,
        user_type=payload.user_type,
        organization_name=payload.organization_name,
        channel=payload.channel,
    )
    return RegisterStartOut(resend_after_seconds=cooldown, deep_link=deep_link)

# Подтверждение кода
@router.post("/register/complete", response_model=TokenPairOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register_complete(
    request: Request,
    payload: RegisterCompleteIn,
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
@limiter.limit("3/minute")
async def register_resend(
    request: Request,
    payload: ResendCodeIn,
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    cooldown, deep_link = await service.register_resend_code(payload.phone, payload.channel)
    return RegisterStartOut(resend_after_seconds=cooldown, deep_link=deep_link)

# Вход для администратора / оператора
@router.post("/admin-login", response_model=TokenPairOut)
@limiter.limit("5/minute")
async def admin_login(
    request: Request,
    payload: AdminLoginIn,
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
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: LoginIn,
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
@limiter.limit("20/minute")
async def refresh(
    request: Request,
    payload: RefreshIn,
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
@limiter.limit("5/minute")
async def password_reset_start(
    request: Request,
    payload: PasswordResetStartIn,
    session: AsyncSession = Depends(get_session),
):
    service = AuthService(session)
    cooldown, deep_link = await service.password_reset_start(payload.phone, payload.channel)
    return PasswordResetStartOut(resend_after_seconds=cooldown, deep_link=deep_link)

# Восстановление пароля: новый пароль
@router.post("/password-reset/complete", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
async def password_reset_complete(
    request: Request,
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

# Смена номера телефона — заявка администратору.
#
# Самостоятельной смены номера нет и не может быть: SMS отключены полностью,
# а код доставляется в мессенджер по user_id, то есть в собственный Telegram/Viber
# заявителя — владение новым номером он не подтверждает вообще. Прежние
# /change-phone/start и /change-phone/complete позволяли любому пользователю
# поставить своему аккаунту любой незанятый номер: занять чужой (владелец после
# этого не мог зарегистрироваться) или показывать сотрудникам чужой телефон
# в заявках и чатах. Владение проверяет администратор вне системы.
@router.post("/change-phone/request", response_model=PhoneChangeRequestOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def create_phone_change_request(
    request: Request,
    payload: PhoneChangeRequestCreateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await PhoneChangeService(session).create_request(
        user=current_user,
        new_phone=payload.new_phone,
        user_comment=payload.user_comment,
    )

# Своя активная заявка (null — активной нет)
@router.get("/change-phone/request", response_model=PhoneChangeRequestOut | None)
async def get_my_phone_change_request(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await PhoneChangeService(session).get_my_request(current_user.id)

# Отозвать свою заявку (например, ошибся номером)
@router.delete("/change-phone/request", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_my_phone_change_request(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await PhoneChangeService(session).cancel_my_request(current_user.id)