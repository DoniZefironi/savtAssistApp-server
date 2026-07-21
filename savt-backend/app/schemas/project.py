from datetime import datetime
from pydantic import BaseModel, Field


class ProjectCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class ProjectUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)


class ProjectCabinetItem(BaseModel):
    id: int
    type: str
    object_number: str
    admin_internal_name: str | None


class ProjectOut(BaseModel):
    id: int
    name: str
    unique_code: str
    parent_project_id: int | None
    cabinets: list[ProjectCabinetItem] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListOut(BaseModel):
    id: int
    name: str
    unique_code: str
    cabinet_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class CabinetProjectPatchIn(BaseModel):
    project_id: int | None = Field(None, gt=0)


class UserProjectListItemOut(BaseModel):
    project_id: int
    name: str
    is_primary: bool
    cabinet_count: int


class UserProjectDetailOut(BaseModel):
    project_id: int
    name: str
    is_primary: bool
    cabinets: list[ProjectCabinetItem] = []


class AddProjectByQrIn(BaseModel):
    qr_data: str = Field(..., min_length=1, max_length=200)

    def parse_unique_code(self) -> str:
        prefix = "savt://project/"
        if self.qr_data.startswith(prefix):
            return self.qr_data[len(prefix):]
        return self.qr_data


class AddProjectByQrOut(BaseModel):
    status: str
    message: str
