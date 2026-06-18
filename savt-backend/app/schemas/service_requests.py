from datetime import datetime
from pydantic import BaseModel, Field


_REQUEST_TYPES = ["repair", "maintenance", "inspection", "other"]
_STATUSES = ["open", "in_progress", "closed"]


class ServiceRequestCreateIn(BaseModel):
    cabinet_id: int = Field(..., gt=0)
    request_type: str = Field(..., pattern="^(repair|maintenance|inspection|other)$")
    description: str = Field(..., min_length=10, max_length=2000)


class ServiceRequestOut(BaseModel):
    id: int
    user_id: int
    cabinet_id: int
    cabinet_object_number: str
    request_type: str
    description: str
    status: str
    created_at: datetime
    closed_at: datetime | None


class ServiceRequestDetailOut(ServiceRequestOut):
    user_full_name: str | None
    user_phone: str | None
    user_type: str | None
    organization_name: str | None
    user_is_verified: bool
    user_registered_at: datetime


class ServiceRequestStatusIn(BaseModel):
    status: str = Field(..., pattern="^(open|in_progress|closed)$")
