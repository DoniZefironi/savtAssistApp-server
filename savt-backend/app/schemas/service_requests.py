from datetime import datetime
from pydantic import BaseModel, Field, model_validator


_REQUEST_TYPES = ["repair", "diagnostics", "remote_adjustment", "onsite_adjustment", "other"]
_STATUSES = ["open", "in_progress", "postponed", "closed"]


class ServiceRequestCreateIn(BaseModel):
    # Ровно одно из двух — заявка либо по конкретному ШУ, либо по проекту в
    # целом (та же схема, что у Document/CabinetPhoto/Chat)
    cabinet_id: int | None = Field(None, gt=0)
    project_id: int | None = Field(None, gt=0)
    request_type: str = Field(..., pattern="^(repair|diagnostics|remote_adjustment|onsite_adjustment|other)$")
    description: str = Field(..., min_length=10, max_length=2000)
    # Ключ идемпотентности оффлайн-очереди (tempId) — повторная отправка с тем
    # же токеном вернёт уже созданную заявку вместо дубля. Необязателен.
    client_token: str | None = Field(None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_cabinet_xor_project(self) -> "ServiceRequestCreateIn":
        if (self.cabinet_id is None) == (self.project_id is None):
            raise ValueError("Нужно указать ровно одно из cabinet_id/project_id")
        return self


class ServiceRequestOut(BaseModel):
    id: int
    user_id: int
    cabinet_id: int | None
    cabinet_object_number: str | None
    project_id: int | None
    project_name: str | None
    request_type: str
    description: str
    status: str
    bitrix_task_id: str | None = None
    chat_id: int | None = None
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
    status: str = Field(..., pattern="^(open|in_progress|postponed|closed)$")
