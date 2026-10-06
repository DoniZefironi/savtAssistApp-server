from datetime import datetime
from pydantic import BaseModel, Field

from app.core.signed_urls import SignedUrl


class AdditionRequestOut(BaseModel):
    id: int
    user_id: int
    user_full_name: str | None
    user_phone: str | None
    user_type: str | None
    organization_name: str | None
    user_is_verified: bool
    user_registered_at: datetime
    project_id: int | None
    project_name: str | None
    photo_url: SignedUrl
    user_comment: str | None
    status: str
    cabinet_id: int | None
    admin_response: str | None
    resolved_by_admin_id: int | None
    resolved_by_admin_name: str | None = None
    created_at: datetime
    resolved_at: datetime | None


class ApproveAdditionIn(BaseModel):
    cabinet_id: int = Field(..., gt=0)
    admin_response: str | None = Field(None, min_length=1, max_length=1000)


class RejectRequestIn(BaseModel):
    admin_response: str = Field(..., min_length=1, max_length=1000)


# Общая форма одобрения с необязательным комментарием — используется там, где
# само одобрение не требует больше никаких данных (сменить телефон, сбросить
# пароль): app/routers/admin_phone_change_requests.py, admin_password_reset_requests.py
class AdminResponseIn(BaseModel):
    admin_response: str | None = Field(None, min_length=1, max_length=1000)

