from datetime import datetime
from pydantic import BaseModel, Field, model_validator


class CabinetCreateIn(BaseModel):
    type: str = Field(..., min_length=1, max_length=100)
    object_number: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    warranty_starts_at: datetime
    warranty_ends_at: datetime
    admin_internal_name: str | None = Field(None, min_length=1, max_length=200)
    admin_comment: str | None = Field(None, max_length=2000)
    purpose: str | None = Field(None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_warranty_dates(self) -> "CabinetCreateIn":
        if self.warranty_ends_at <= self.warranty_starts_at:
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


class CabinetOut(BaseModel):
    id: int
    unique_code: str
    type: str
    object_number: str
    description: str | None
    warranty_starts_at: datetime
    warranty_ends_at: datetime
    admin_internal_name: str | None
    admin_comment: str | None
    purpose: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CabinetListOut(BaseModel):
    id: int
    unique_code: str
    object_number: str
    warranty_starts_at: datetime
    warranty_ends_at: datetime
    admin_internal_name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCabinetListItemOut(BaseModel):
    cabinet_id: int
    type: str
    object_number: str
    warranty_ends_at: datetime
    warranty_status: str
    custom_name: str | None
    is_primary: bool
    unread_count: int


class UserCabinetDetailOut(BaseModel):
    cabinet_id: int
    type: str
    object_number: str
    description: str | None
    purpose: str | None
    warranty_starts_at: datetime
    warranty_ends_at: datetime
    warranty_status: str
    custom_name: str | None
    custom_comment: str | None
    is_primary: bool


class UserCabinetPatchIn(BaseModel):
    custom_name: str | None = Field(None, min_length=1, max_length=200)
    custom_comment: str | None = None


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
