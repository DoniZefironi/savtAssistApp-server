from datetime import datetime
from typing import Literal

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


class UnreadCountOut(BaseModel):
    unread: int


class NotificationSettingsOut(BaseModel):
    chat_messages: bool
    promotional: bool
    warranty_expiring: bool
    request_status_change: bool
    # Пауза. is_muted — готовый ответ на вопрос «сейчас тихо?», чтобы клиент не
    # сравнивал даты сам и не разошёлся с сервером на границе срока
    is_muted: bool = False
    muted_until: datetime | None = None
    muted_indefinitely: bool = False

    model_config = {"from_attributes": True}


class MuteIn(BaseModel):
    """hours=null — бессрочно. Список часов закрытый: это кнопки в интерфейсе,
    а не свободный ввод."""
    hours: Literal[1, 2, 3, 6, 12, 24] | None = None


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


class BroadcastResultOut(BaseModel):
    sent_to: int
    skipped_opted_out: int


class PromoMessageOut(BaseModel):
    """Заготовка рекламного уведомления из файла (см. PROMO_MESSAGES_FILE)."""
    id: str
    title: str
    body: str
    data: dict = {}


class PromoSendResultOut(BaseModel):
    sent_to: int
    skipped_opted_out: int
    message: PromoMessageOut | None = None
