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


# Валидация
# Регистрация
class RegisterStartIn(BaseModel):
    phone: str = Field(...)
    password: str = Field(..., min_length=8, max_length=100)
    password_confirm: str = Field(..., min_length=8, max_length=100)
    full_name: str = Field(..., max_length=200)
    user_type: str = Field(...)
    organization_name: str | None = Field(None)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)
    
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
        if self.user_type == "organization" and not self.organization_name:
            raise ValueError('Для типа пользователя "организация" необходимо указать наименование организации')
        return self

class ResendCodeIn(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)


class RegisterStartOut(BaseModel):
    message: str = "Код подтверждения отправлен"
    resend_after_seconds: int


class RegisterCompleteIn(BaseModel):
    phone: str
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)

# Аутентификация
class LoginIn(BaseModel):
    phone: str
    password: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)
    
class AdminLoginIn(BaseModel):
    login: str
    password: str


# Токены
class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

# Профиль пользователя
class UserMeOut(BaseModel):
    id: int
    phone: str | None
    email: str | None
    user_type: str | None
    organization_name: str | None
    full_name: str | None
    role: str
    is_phone_verified: bool

    model_config = {"from_attributes": True}

# Сброс пароля
class PasswordResetStartIn(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)


class PasswordResetStartOut(BaseModel):
    message: str = "На телефон отправлен код"
    resend_after_seconds: int


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