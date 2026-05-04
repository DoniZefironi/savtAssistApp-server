from pydantic import BaseModel, Field, field_validator


def _normalize_phone(phone: str) -> str:
    cleaned = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if not cleaned.startswith("+"):
        raise ValueError("Телефон должен начинаться с '+', например +375296083352")
    if len(cleaned) < 11 or len(cleaned) > 16:
        raise ValueError("Некорректная длина телефона")
    return cleaned


class RegisterStartIn(BaseModel):
    phone: str
    password: str = Field(..., min_length=8, max_length=100)
    full_name: str | None = Field(None, max_length=200)

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


class LoginIn(BaseModel):
    phone: str
    password: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserMeOut(BaseModel):
    id: int
    phone: str
    full_name: str | None
    role: str
    is_phone_verified: bool

    model_config = {"from_attributes": True}


class PasswordResetStartIn(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)


class PasswordResetStartOut(BaseModel):
    message: str = "Если такой пользователь существует, на телефон отправлен код"
    resend_after_seconds: int


class PasswordResetCompleteIn(BaseModel):
    phone: str
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=100)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _normalize_phone(v)