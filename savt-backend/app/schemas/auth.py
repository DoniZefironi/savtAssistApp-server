from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator
import phonenumbers

# Нормализируем телефон
def _normalize_phone(phone: str) -> str:
    try:
        # парсим номер
        parsed = phonenumbers.parse(phone, None)
        # проверяем, что номер возможный
        if not phonenumbers.is_possible_number(parsed):
            raise ValueError("Номер телефона невозможен")
        # проверяем, что номер валидный
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("Номер телефона недействителен")
        # возвращаем в международном формате
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException as e:
        raise ValueError(f"Неверный формат телефона: {e}")


# Канал доставки кода подтверждения. SMS отключено, Viber удалён: подтвердить
# владение номером через него нечем — аналога Telegram request_contact нет.
# Поле оставлено в запросах ради совместимости с клиентами, но значение может
# быть только "telegram".
def _validate_channel(v: str) -> str:
    allowed = ("telegram",)
    if v not in allowed:
        raise ValueError(f"channel должен быть один из {', '.join(allowed)}")
    return v


# Валидация
# Регистрация
class RegisterStartIn(BaseModel):
    # Номер телефона здесь НЕ запрашивается: он придёт из контакта Telegram и
    # потому будет подтверждён по построению. contact_phone — необязательный
    # рабочий номер, он не подтверждается и на вход не влияет.
    password: str = Field(..., min_length=8, max_length=100)
    password_confirm: str = Field(..., min_length=8, max_length=100)
    full_name: str = Field(..., min_length=1, max_length=200)
    user_type: str = Field(...)
    organization_name: str | None = Field(None)
    contact_phone: str | None = Field(None)

    @field_validator("contact_phone")
    @classmethod
    def validate_contact_phone(cls, v: str | None) -> str | None:
        return _normalize_phone(v) if v else None

    @field_validator("user_type")
    @classmethod
    def validate_user_type(cls, v: str) -> str:
        allowed_types = ["individual", "organization"]
        if v not in allowed_types:
            raise ValueError(f"user_type должен быть один из {', '.join(allowed_types)}")
        return v
    
    @model_validator(mode='after')
    def validate_passwords_match(self) -> 'RegisterStartIn':
        if self.password != self.password_confirm:
            raise ValueError('Пароли не совпадают')
        return self
    
    @model_validator(mode='after')
    def validate_organization_name_for_contractor(self) -> 'RegisterStartIn':
        if self.user_type == "organization" and not (self.organization_name or "").strip():
            raise ValueError('Для типа пользователя "организация" необходимо указать наименование организации')
        return self

class ResendCodeIn(BaseModel):
    # Телефон клиенту неизвестен, пока пользователь не поделился контактом
    # в Telegram, поэтому регистрация опознаётся по своему токену
    registration_token: str = Field(..., min_length=1, max_length=64)


class RegisterStartOut(BaseModel):
    message: str = "Откройте Telegram и подтвердите номер"
    # Хранить у себя до конца регистрации: им же идентифицируются
    # /register/complete и /register/resend
    registration_token: str
    # Ссылка на бота. Всегда непустая на старте регистрации: номер ещё
    # неизвестен, подтвердить его можно только в Telegram
    deep_link: str
    resend_after_seconds: int


class RegisterCompleteIn(BaseModel):
    registration_token: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=6, max_length=6)


class ResendCodeOut(BaseModel):
    message: str = "Код отправлен повторно"
    resend_after_seconds: int
    # Не None, если номер ещё не подтверждён: надо вернуться в Telegram
    # и нажать «Отправить мой номер» — кода до этого не существует
    deep_link: str | None = None

# Аутентификация
class LoginIn(BaseModel):
    phone: str
    password: str = Field(..., min_length=8, max_length=100)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)

class AdminLoginIn(BaseModel):
    login: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=8, max_length=100)


# Токены
class RefreshIn(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutIn(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

# Профиль пользователя
class UserMeOut(BaseModel):
    id: int
    # подтверждённый номер из Telegram, он же логин — меняется только через заявку
    phone: str | None
    # необязательный рабочий номер, меняется свободно через PATCH /auth/me
    contact_phone: str | None
    email: str | None
    user_type: str | None
    organization_name: str | None
    full_name: str | None
    role: str
    is_phone_verified: bool
    is_verified: bool

    model_config = {"from_attributes": True}

# Сброс пароля
class PasswordResetStartIn(BaseModel):
    phone: str
    channel: str = Field(...)  # только "telegram"

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        return _validate_channel(v)


class PasswordResetStartOut(BaseModel):
    message: str = "На телефон отправлен код"
    resend_after_seconds: int
    deep_link: str | None = None


class PasswordResetCompleteIn(BaseModel):
    phone: str
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=100)
    new_password_confirm: str = Field(..., min_length=8, max_length=100)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)
    
    @model_validator(mode='after')
    def validate_passwords_match(self) -> 'PasswordResetCompleteIn':
        if self.new_password != self.new_password_confirm:
            raise ValueError('Пароли не совпадают')
        return self
    
# Редактирование профиля
class UpdateProfileIn(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    email: str | None = Field(None, max_length=100)
    organization_name: str | None = Field(None, min_length=1, max_length=255)
    # Рабочий номер. Не подтверждается и на вход не влияет — в отличие от phone,
    # который берётся из Telegram и меняется только через заявку админу.
    contact_phone: str | None = Field(None, max_length=20)

    @field_validator("contact_phone")
    @classmethod
    def validate_contact_phone(cls, v: str | None) -> str | None:
        return _normalize_phone(v) if v else None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        import re
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v):
            raise ValueError("Некорректный email")
        return v.lower()


# Смена номера телефона — только через заявку с ручным одобрением админом.
# Самостоятельной смены нет и быть не может: SMS отключены, а код уходит в
# мессенджер самого заявителя и владение новым номером не подтверждает.
class PhoneChangeRequestCreateIn(BaseModel):
    new_phone: str
    user_comment: str | None = Field(None, min_length=1, max_length=1000)

    @field_validator("new_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)


class PhoneChangeRequestOut(BaseModel):
    id: int
    user_id: int
    new_phone: str
    old_phone: str | None
    user_comment: str | None
    status: str
    admin_response: str | None
    resolved_by_admin_id: int | None
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


# Админский список — с данными заявителя, как в остальных заявках
class AdminPhoneChangeRequestOut(PhoneChangeRequestOut):
    user_full_name: str | None
    user_type: str | None
    organization_name: str | None
    user_is_verified: bool
    user_registered_at: datetime
    # Сколько ещё pending-заявок на этот же номер, включая текущую. >1 означает,
    # что за номер борются несколько аккаунтов — одобрять надо осознанно
    pending_rivals: int = 1


# смена пароля
class PasswordChange(BaseModel):
    password: str = Field(..., min_length=8, max_length=100)
    new_password: str = Field(..., min_length=8, max_length=100)
    new_password_confirm: str = Field(..., min_length=8, max_length=100)
    
    @model_validator(mode='after')
    def validate_passwords_match(self) -> 'PasswordChange':
        if self.new_password != self.new_password_confirm:
            raise ValueError('Пароли не совпадают')
        return self
    
    @model_validator(mode='after')
    def validate_passwords_different(self) -> 'PasswordChange':
        if self.password == self.new_password:
            raise ValueError('Новый пароль должен отличаться от старого')
        return self