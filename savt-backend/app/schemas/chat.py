import re
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class AttachmentOut(BaseModel):
    id: int
    attachment_type: str
    file_url: str | None
    file_name: str | None
    file_size_bytes: int | None
    mime_type: str | None
    duration_seconds: int | None
    latitude: float | None = None
    longitude: float | None = None

    model_config = {"from_attributes": True}


class ReactionOut(BaseModel):
    id: int
    user_id: int
    emoji: str

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    chat_id: int
    sender_id: int
    sender_name: str | None
    text: str | None
    reply_to_message_id: int | None
    is_read: bool
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None
    attachments: list[AttachmentOut] = []
    reactions: list[ReactionOut] = []


class ChatAttachmentOut(BaseModel):
    id: int
    message_id: int
    attachment_type: str
    file_url: str | None
    file_name: str | None
    file_size_bytes: int | None
    mime_type: str | None
    duration_seconds: int | None
    latitude: float | None = None
    longitude: float | None = None
    created_at: datetime


class MessageSearchOut(BaseModel):
    id: int
    chat_id: int
    chat_type: str
    cabinet_object_number: str | None
    chat_user_id: int
    sender_id: int
    sender_name: str | None
    text: str | None
    created_at: datetime
    attachments: list[AttachmentOut] = []


class AttachmentIn(BaseModel):
    # Файловое вложение — все 4 поля обязательны вместе.
    file_url: str | None = Field(None, max_length=500)
    file_name: str | None = Field(None, min_length=1, max_length=255)
    file_size_bytes: int | None = Field(None, gt=0)
    mime_type: str | None = Field(None, max_length=100)
    duration_seconds: int | None = Field(None, ge=0)
    # Геолокация — вместо файла, latitude/longitude обязательны вместе
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_file_xor_location(self) -> "AttachmentIn":
        is_location = self.latitude is not None or self.longitude is not None
        is_file = any([self.file_url, self.file_name, self.file_size_bytes, self.mime_type])
        if is_location and is_file:
            raise ValueError("Вложение должно быть либо файлом, либо геолокацией, не тем и другим")
        if is_location and (self.latitude is None or self.longitude is None):
            raise ValueError("Для геолокации нужны и latitude, и longitude")
        if is_file and not all([self.file_url, self.file_name, self.file_size_bytes, self.mime_type]):
            raise ValueError("Для файлового вложения нужны file_url, file_name, file_size_bytes и mime_type")
        if not is_location and not is_file:
            raise ValueError("Вложение должно содержать либо файл, либо геолокацию")
        return self


class MessageCreateIn(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=4000)
    reply_to_message_id: int | None = Field(None, gt=0)
    attachments: list[AttachmentIn] = []

    @model_validator(mode="after")
    def must_have_content(self) -> "MessageCreateIn":
        if not self.text and not self.attachments:
            raise ValueError("Сообщение должно содержать текст или вложение")
        return self


class ChatListOut(BaseModel):
    id: int
    chat_type: str
    cabinet_id: int | None
    cabinet_name: str | None
    cabinet_object_number: str | None = None
    user_id: int | None = None
    user_name: str | None = None
    last_message_text: str | None
    last_message_at: datetime | None
    unread_count: int
    problem_status: str
    bot_active: bool
    operator_requested: bool
    service_request_id: int | None = None
    service_request_type: str | None = None
    service_request_status: str | None = None
    service_request_description: str | None = None
    service_request_created_at: datetime | None = None
    archived_at: datetime | None = None


class WallpaperIn(BaseModel):
    wallpaper_url: str | None = Field(None, max_length=500)


class ChatOut(BaseModel):
    id: int
    chat_type: str
    cabinet_id: int | None
    service_request_id: int | None = None
    service_request_type: str | None = None
    service_request_status: str | None = None
    service_request_description: str | None = None
    service_request_created_at: datetime | None = None
    problem_status: str
    bot_active: bool
    operator_requested: bool
    archived_at: datetime | None = None
    created_at: datetime


_COLOR_FIELDS = (
    "own_bubble_color", "other_bubble_color", "bot_bubble_color",
    "own_text_color", "other_text_color", "bot_text_color", "nick_color",
)


class ChatSettingsIn(BaseModel):
    own_bubble_color: str | None = Field(None, max_length=7)
    other_bubble_color: str | None = Field(None, max_length=7)
    bot_bubble_color: str | None = Field(None, max_length=7)
    own_text_color: str | None = Field(None, max_length=7)
    other_text_color: str | None = Field(None, max_length=7)
    bot_text_color: str | None = Field(None, max_length=7)
    nick_color: str | None = Field(None, max_length=7)
    font_size: int | None = Field(None, ge=8, le=24)
    wallpaper_url: str | None = Field(None, max_length=500)
    wallpaper_id: str | None = Field(None, max_length=50)

    @field_validator(*_COLOR_FIELDS, mode="before")
    @classmethod
    def validate_hex_color(cls, v: object) -> object:
        if v is not None and not _HEX_COLOR_RE.match(str(v)):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        return v


class ChatSettingsOut(BaseModel):
    user_id: int
    chat_id: int | None
    own_bubble_color: str | None
    other_bubble_color: str | None
    bot_bubble_color: str | None
    own_text_color: str | None
    other_text_color: str | None
    bot_text_color: str | None
    nick_color: str | None
    font_size: int | None
    wallpaper_url: str | None
    wallpaper_id: str | None

    model_config = {"from_attributes": True}
