from datetime import datetime
from pydantic import BaseModel, Field


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
    created_at: datetime
    cabinets: list[AdminUserCabinetItem]


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
