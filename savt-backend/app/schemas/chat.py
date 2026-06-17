from datetime import datetime
from pydantic import BaseModel, Field, model_validator


class AttachmentOut(BaseModel):
    id: int
    attachment_type: str
    file_url: str
    file_name: str
    file_size_bytes: int
    mime_type: str
    duration_seconds: int | None

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


class AttachmentIn(BaseModel):
    file_url: str = Field(..., max_length=500)
    file_name: str = Field(..., min_length=1, max_length=255)
    file_size_bytes: int = Field(..., gt=0)
    mime_type: str = Field(..., max_length=100)
    duration_seconds: int | None = Field(None, ge=0)


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


class WallpaperIn(BaseModel):
    wallpaper_url: str | None = Field(None, max_length=500)


class ChatOut(BaseModel):
    id: int
    chat_type: str
    cabinet_id: int | None
    problem_status: str
    bot_active: bool
    operator_requested: bool
    wallpaper_url: str | None
    pinned_message_id: int | None
    created_at: datetime
