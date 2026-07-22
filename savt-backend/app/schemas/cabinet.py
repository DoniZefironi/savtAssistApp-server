from datetime import datetime
from pydantic import BaseModel, Field, model_validator

from app.schemas.tags import TagOut


class CabinetGeoItem(BaseModel):
    id: int
    object_number: str
    admin_internal_name: str | None
    warranty_status: str
    latitude: float | None
    longitude: float | None
    has_open_requests: bool


class CabinetCreateIn(BaseModel):
    type: str = Field(..., min_length=1, max_length=100)
    object_number: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    warranty_starts_at: datetime | None = None
    warranty_ends_at: datetime | None = None
    admin_internal_name: str | None = Field(None, min_length=1, max_length=200)
    admin_comment: str | None = Field(None, max_length=2000)
    purpose: str | None = Field(None, min_length=1, max_length=200)
    latitude: float | None = Field(None, ge=-90.0, le=90.0)
    longitude: float | None = Field(None, ge=-180.0, le=180.0)

    @model_validator(mode="after")
    def validate_warranty_dates(self) -> "CabinetCreateIn":
        s, e = self.warranty_starts_at, self.warranty_ends_at
        if s is not None and e is not None and e <= s:
            raise ValueError("Дата окончания гарантии должна быть позже даты начала")
        return self


class CabinetUpdateIn(BaseModel):
    type: str | None = Field(None, min_length=1, max_length=100)
    object_number: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    warranty_starts_at: datetime | None = None
    warranty_ends_at: datetime | None = None
    admin_internal_name: str | None = Field(None, min_length=1, max_length=200)
    admin_comment: str | None = Field(None, max_length=2000)
    purpose: str | None = Field(None, min_length=1, max_length=200)
    latitude: float | None = Field(None, ge=-90.0, le=90.0)
    longitude: float | None = Field(None, ge=-180.0, le=180.0)

    @model_validator(mode="after")
    def validate_warranty_dates(self) -> "CabinetUpdateIn":
        s, e = self.warranty_starts_at, self.warranty_ends_at
        if s is not None and e is not None and e <= s:
            raise ValueError("Дата окончания гарантии должна быть позже даты начала")
        return self


class CabinetOut(BaseModel):
    id: int
    unique_code: str
    type: str
    object_number: str
    description: str | None
    warranty_starts_at: datetime | None
    warranty_ends_at: datetime | None
    admin_internal_name: str | None
    admin_comment: str | None
    purpose: str | None
    latitude: float | None
    longitude: float | None
    tags: list[TagOut] = []
    project_id: int | None
    project_name: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CabinetListOut(BaseModel):
    id: int
    unique_code: str
    type: str
    object_number: str
    purpose: str | None
    warranty_starts_at: datetime | None
    warranty_ends_at: datetime | None
    warranty_status: str
    admin_internal_name: str | None
    admin_comment: str | None
    tags: list[TagOut] = []
    project_id: int | None
    project_name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCabinetListItemOut(BaseModel):
    cabinet_id: int
    type: str
    object_number: str
    warranty_ends_at: datetime | None
    warranty_status: str
    custom_name: str | None
    is_primary: bool
    unread_count: int
    project_id: int | None
    project_name: str | None


class UserCabinetDetailOut(BaseModel):
    cabinet_id: int
    type: str
    object_number: str
    description: str | None
    purpose: str | None
    warranty_starts_at: datetime | None
    warranty_ends_at: datetime | None
    warranty_status: str
    latitude: float | None
    longitude: float | None
    custom_name: str | None
    custom_comment: str | None
    is_primary: bool
    project_id: int | None
    project_name: str | None


class UserCabinetPatchIn(BaseModel):
    custom_name: str | None = Field(None, min_length=1, max_length=200)
    custom_comment: str | None = Field(None, max_length=2000)


class AddByQrIn(BaseModel):
    qr_data: str = Field(..., min_length=1, max_length=200)

    def parse_unique_code(self) -> str:
        prefix = "savt://cabinet/"
        if self.qr_data.startswith(prefix):
            return self.qr_data[len(prefix):]
        return self.qr_data


class AddByQrOut(BaseModel):
    status: str
    message: str


class AddByPhotoIn(BaseModel):
    photo_url: str = Field(..., min_length=1, max_length=500)
    user_comment: str | None = Field(None, min_length=1, max_length=1000)


class AddByPhotoOut(BaseModel):
    request_id: int
    message: str = "Заявка отправлена на рассмотрение"
