from datetime import datetime
from pydantic import BaseModel, Field


class AdditionRequestOut(BaseModel):
    id: int
    user_id: int
    user_full_name: str | None
    user_phone: str | None
    photo_url: str
    user_comment: str | None
    status: str
    cabinet_id: int | None
    admin_response: str | None
    created_at: datetime
    resolved_at: datetime | None


class ShareRequestOut(BaseModel):
    id: int
    user_id: int
    user_full_name: str | None
    user_phone: str | None
    cabinet_id: int
    cabinet_type: str
    cabinet_object_number: str
    user_comment: str | None
    status: str
    admin_response: str | None
    created_at: datetime
    resolved_at: datetime | None


class ApproveAdditionIn(BaseModel):
    cabinet_id: int
    admin_response: str | None = Field(None, min_length=1, max_length=1000)


class RejectRequestIn(BaseModel):
    admin_response: str = Field(..., min_length=1)


class ApproveShareIn(BaseModel):
    admin_response: str | None = Field(None, min_length=1, max_length=1000)
