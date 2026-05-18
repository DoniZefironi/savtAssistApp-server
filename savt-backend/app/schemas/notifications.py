from datetime import datetime
from pydantic import BaseModel, Field


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    body: str
    data: dict
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationSettingsOut(BaseModel):
    chat_messages: bool
    promotional: bool
    warranty_expiring: bool
    request_status_change: bool

    model_config = {"from_attributes": True}


class NotificationSettingsPatchIn(BaseModel):
    chat_messages: bool | None = None
    promotional: bool | None = None
    warranty_expiring: bool | None = None
    request_status_change: bool | None = None


class DeviceTokenIn(BaseModel):
    token: str = Field(..., min_length=1, max_length=500)
    platform: str = Field(..., pattern="^(ios|android)$")


class BroadcastIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    body: str = Field(..., min_length=1, max_length=1000)
    role: str | None = Field(None, pattern="^(user|operator|admin)$")
