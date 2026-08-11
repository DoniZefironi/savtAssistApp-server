from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from app.schemas.project import UserProjectListItemOut


class AdminUserListOut(BaseModel):
    id: int
    phone: str | None
    contact_phone: str | None = None
    login: str | None
    full_name: str | None
    user_type: str | None
    organization_name: str | None
    role: str
    is_active: bool
    is_phone_verified: bool
    is_verified: bool
    created_at: datetime


class AdminUserDetailOut(BaseModel):
    id: int
    phone: str | None
    contact_phone: str | None = None
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
    # Проекты, в которых состоит пользователь — не отдельные ШУ: доступ к
    # шкафам выводится из проекта целиком, показывать его по-шкафно больше не
    # имеет смысла (щёлкнули по строке — переход на карточку проекта, не ШУ)
    projects: list[UserProjectListItemOut]


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
    """Пользователь с доступом к ШУ — на самом деле участник проекта, которому
    принадлежит шкаф (доступ выводится из проекта, не хранится по-шкафно).
    is_primary/added_at — тоже проектные, не привязаны к конкретному ШУ."""
    user_id: int
    full_name: str | None
    phone: str | None
    user_type: str | None
    is_primary: bool
    custom_name: str | None
    added_at: datetime


class ProjectUserOut(BaseModel):
    user_id: int
    full_name: str | None
    phone: str | None
    user_type: str | None
    is_primary: bool
    added_at: datetime


class RemoveUserFromProjectIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)
