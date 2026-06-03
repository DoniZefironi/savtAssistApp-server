from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class AdminUserListOut(BaseModel):
    id: int
    phone: str | None
    login: str | None
    full_name: str | None
    user_type: str | None
    organization_name: str | None
    role: str
    is_active: bool
    is_phone_verified: bool
    is_verified: bool
    created_at: datetime


class AdminUserCabinetItem(BaseModel):
    cabinet_id: int
    type: str
    object_number: str
    warranty_ends_at: datetime
    warranty_status: str
    custom_name: str | None
    is_primary: bool
    added_at: datetime


class AdminUserDetailOut(BaseModel):
    id: int
    phone: str | None
    login: str | None
    full_name: str | None
    email: str | None
    user_type: str | None
    organization_name: str | None
    role: str
    is_active: bool
    is_phone_verified: bool
    is_verified: bool
    created_at: datetime
    cabinets: list[AdminUserCabinetItem]


class CreateOperatorIn(BaseModel):
    login: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=100)
    full_name: str | None = Field(None, max_length=200)

    @field_validator("login")
    @classmethod
    def login_no_spaces(cls, v: str) -> str:
        if " " in v:
            raise ValueError("Логин не должен содержать пробелы")
        return v.lower()


class CreateAdminIn(BaseModel):
    login: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=100)
    full_name: str | None = Field(None, max_length=200)

    @field_validator("login")
    @classmethod
    def login_no_spaces(cls, v: str) -> str:
        if " " in v:
            raise ValueError("Логин не должен содержать пробелы")
        return v.lower()


class BanUserIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class CabinetUserOut(BaseModel):
    user_id: int
    full_name: str | None
    phone: str | None
    user_type: str | None
    is_primary: bool
    custom_name: str | None
    added_at: datetime


class RemoveUserFromCabinetIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)
