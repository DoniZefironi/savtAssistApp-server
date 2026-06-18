from datetime import datetime
from pydantic import BaseModel, Field


from app.schemas.tags import TagOut


class DocumentOut(BaseModel):
    id: int
    cabinet_id: int
    title: str
    doc_type: str
    file_url: str
    file_size_bytes: int
    mime_type: str
    requires_approval: bool
    version: int
    tags: list[TagOut] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserDocumentOut(BaseModel):
    id: int
    cabinet_id: int
    title: str
    doc_type: str
    file_url: str | None  # null если requires_approval=true и нет доступа
    file_size_bytes: int
    mime_type: str
    has_access: bool
    tags: list[TagOut] = []


class PhotoOut(BaseModel):
    id: int
    cabinet_id: int
    url: str
    caption: str | None
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PhotoUpdateIn(BaseModel):
    caption: str | None = Field(None, max_length=500)
    sort_order: int | None = Field(None, ge=0, le=9999)


class DocumentRequestCreateIn(BaseModel):
    user_message: str | None = Field(None, min_length=1, max_length=1000)


class DocumentRequestOut(BaseModel):
    id: int
    user_id: int
    user_full_name: str | None
    user_phone: str | None
    user_type: str | None
    organization_name: str | None
    user_is_verified: bool
    user_registered_at: datetime
    document_id: int | None
    cabinet_id: int | None
    doc_type: str
    status: str
    user_message: str | None
    admin_response: str | None
    created_at: datetime
    resolved_at: datetime | None


class ApproveDocumentRequestIn(BaseModel):
    admin_response: str | None = None


class RejectDocumentRequestIn(BaseModel):
    admin_response: str = Field(..., min_length=1, max_length=1000)
