from datetime import datetime
from pydantic import BaseModel, Field


class AdditionRequestOut(BaseModel):
    id: int
    user_id: int
    user_full_name: str | None
    user_phone: str | None
    user_type: str | None
    organization_name: str | None
    user_is_verified: bool
    user_registered_at: datetime
    photo_url: str
    user_comment: str | None
    status: str
    cabinet_id: int | None
    admin_response: str | None
    resolved_by_admin_id: int | None
    created_at: datetime
    resolved_at: datetime | None


class ShareRequestOut(BaseModel):
    id: int
    user_id: int
    user_full_name: str | None
    user_phone: str | None
    user_type: str | None
    organization_name: str | None
    user_is_verified: bool
    user_registered_at: datetime
    cabinet_id: int
    cabinet_type: str
    cabinet_object_number: str
    user_comment: str | None
    status: str
    admin_response: str | None
    resolved_by_admin_id: int | None
    created_at: datetime
    resolved_at: datetime | None


class ApproveAdditionIn(BaseModel):
    cabinet_id: int = Field(..., gt=0)
    admin_response: str | None = Field(None, min_length=1, max_length=1000)


class RejectRequestIn(BaseModel):
    admin_response: str = Field(..., min_length=1, max_length=1000)


class ApproveShareIn(BaseModel):
    admin_response: str | None = Field(None, min_length=1, max_length=1000)


class ProjectShareRequestOut(BaseModel):
    id: int
    user_id: int
    user_full_name: str | None
    user_phone: str | None
    user_type: str | None
    organization_name: str | None
    user_is_verified: bool
    user_registered_at: datetime
    project_id: int
    project_name: str
    user_comment: str | None
    status: str
    admin_response: str | None
    resolved_by_admin_id: int | None
    created_at: datetime
    resolved_at: datetime | None
