from datetime import datetime
from pydantic import BaseModel, Field
from app.utils.qr_code import clean_code


class ProjectUpdateIn(BaseModel):
    parent_project_id: int | None = Field(None, gt=0)
    # Название — тоже из Bitrix (title сделки), сюда не принимается: правка
    # затёрлась бы на ближайшем ONCRMDEALUPDATE, а до тех пор карточка расходилась
    # бы с CRM. Переименовать проект можно только сменой названия сделки.
    # Гарантия — единственное, что администратор реально может править: в CRM
    # такого поля нет. Даты отгрузки, компания и контакты тоже из Bitrix и здесь
    # не принимаются — те же соображения, что и с названием.
    warranty_starts_at: datetime | None = None
    warranty_ends_at: datetime | None = None


class ProjectCabinetItem(BaseModel):
    id: int
    type: str
    object_number: str
    admin_internal_name: str | None


class ProjectContactOut(BaseModel):
    """Контактное лицо заказчика. Только для операторов и админов — в
    пользовательских схемах проекта этого блока нет."""
    id: int
    full_name: str | None
    post: str | None
    phones: list[str] = []
    emails: list[str] = []

    model_config = {"from_attributes": True}


class ProjectOut(BaseModel):
    id: int
    name: str
    unique_code: str
    parent_project_id: int | None
    folder_synced_at: datetime | None = None
    cabinets: list[ProjectCabinetItem] = []
    # из Bitrix, только для чтения
    production_number: str | None = None
    shipment_planned_at: datetime | None = None
    shipment_actual_at: datetime | None = None
    company_name: str | None = None
    contacts: list[ProjectContactOut] = []
    # наше, редактируется через PATCH
    warranty_starts_at: datetime | None = None
    warranty_ends_at: datetime | None = None
    warranty_status: str = "none"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListOut(BaseModel):
    id: int
    name: str
    unique_code: str
    production_number: str | None = None
    # Год проекта — тот же, по которому раскладываются папки на NAS и работает
    # фильтр year. Из номера, а при его отсутствии — из даты создания
    year: int
    cabinet_count: int
    company_name: str | None = None
    shipment_planned_at: datetime | None = None
    shipment_actual_at: datetime | None = None
    warranty_ends_at: datetime | None = None
    warranty_status: str = "none"
    created_at: datetime

    model_config = {"from_attributes": True}


class SyncFolderResultOut(BaseModel):
    """Итог ручной сверки папки. Не ProjectOut: кнопку нажимают, чтобы узнать,
    что именно сделалось, а карточка проекта от сверки не меняется."""
    synced_at: datetime | None = None
    imported_documents: int = 0
    # готовая фраза для тоста — считается на сервере, чтобы клиенты не собирали
    # её каждый по-своему
    message: str


class SyncAllFoldersResultOut(BaseModel):
    """Итог ручной сверки папок всех проектов разом (кнопка "синхронизировать всё")."""
    total_projects: int
    synced_projects: int
    # переехали в годовую папку без полной сверки — гарантия истекла, см. is_sync_eligible
    relocated_projects: int
    failed_projects: int
    message: str


class CabinetProjectPatchIn(BaseModel):
    project_id: int | None = Field(None, gt=0)


# Пользовательские схемы: компания, даты и гарантия видны, контактных лиц
# заказчика здесь НЕТ — это персональные данные, а доступ к проекту получают
# по QR-коду, то есть потенциально широкий круг людей
class UserProjectListItemOut(BaseModel):
    project_id: int
    name: str
    is_pinned: bool = False
    cabinet_count: int
    company_name: str | None = None
    warranty_status: str = "none"


class UserProjectDetailOut(BaseModel):
    project_id: int
    name: str
    is_pinned: bool = False
    cabinets: list[ProjectCabinetItem] = []
    company_name: str | None = None
    shipment_planned_at: datetime | None = None
    shipment_actual_at: datetime | None = None
    warranty_starts_at: datetime | None = None
    warranty_ends_at: datetime | None = None
    warranty_status: str = "none"


class AddProjectByQrIn(BaseModel):
    qr_data: str = Field(..., min_length=1, max_length=200)

    def parse_unique_code(self) -> str:
        # Два формата живут параллельно: savt://project/ в кур-коде сканируется
        # прямо в приложении (POST /projects/add-by-qr), /add/project/ —
        # публичная страница для сканирования обычной камерой телефона
        # (см. app/routers/add_qr.py, app/routers/qr.py) — код всегда
        # последним сегментом, независимо от домена/схемы перед ним
        for prefix in ("savt://project/", "/add/project/"):
            idx = self.qr_data.find(prefix)
            if idx != -1:
                return clean_code(self.qr_data[idx + len(prefix):])
        return clean_code(self.qr_data)


class AddProjectByQrOut(BaseModel):
    status: str
    message: str


class DecodeProjectCodeIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=200)


class DecodeProjectCodeOut(BaseModel):
    production_number: str
