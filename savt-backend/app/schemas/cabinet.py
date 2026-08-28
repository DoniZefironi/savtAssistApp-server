from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.signed_urls import strip_signature
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
    # ШУ создаётся только внутри проекта — самостоятельных, ничейных шкафов
    # больше не заводится (см. PATCH /admin/cabinets/{id}/project для отвязки
    # уже существующих)
    project_id: int = Field(..., gt=0)
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
    # Топик MQTT-контроллера этого ШУ ("26_001/1/data") — прокси на C# шлёт вебхук
    # телеметрии с этим топиком как есть, по нему находим cabinet_id
    mqtt_topic: str | None = Field(None, max_length=200)
    # Брокер конкретно этого ШУ (свой у каждого контроллера) — прокси узнаёт про
    # него через GET /webhooks/telemetry/targets, не хранит в своём конфиге
    mqtt_host: str | None = Field(None, max_length=255)
    mqtt_port: int | None = Field(None, ge=1, le=65535)
    mqtt_username: str | None = Field(None, max_length=200)
    mqtt_password: str | None = Field(None, max_length=200)
    # id SIM во внешнем приложении управления SIM-картами (10.1.0.67:5000) —
    # GUID-строка, не число. null — отвязать текущую SIM от ШУ
    sim_id: str | None = None

    @model_validator(mode="after")
    def validate_warranty_dates(self) -> "CabinetUpdateIn":
        s, e = self.warranty_starts_at, self.warranty_ends_at
        if s is not None and e is not None and e <= s:
            raise ValueError("Дата окончания гарантии должна быть позже даты начала")
        return self


class SimInfoOut(BaseModel):
    """Живой снимок SIM из внешнего приложения (10.1.0.67:5000) — не хранится
    у нас, запрашивается заново при каждом показе карточки ШУ, см. sim_service.py.
    Отдаём фронту только то, что реально нужно показать; id — не для показа,
    а чтобы фронт мог сослаться на конкретную SIM при выборе в GET /admin/sim."""
    id: str
    serial_number: str | None = None
    phone: str | None = None
    ip: str | None = None


class CabinetOut(BaseModel):
    id: int
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
    mqtt_topic: str | None
    mqtt_host: str | None
    mqtt_port: int | None
    mqtt_username: str | None
    # mqtt_password намеренно не отдаётся обратно клиенту — write-only, как обычный пароль
    sim_id: str | None
    # None если sim_id не задан, а также если внешний сервис недоступен —
    # отсутствие данных не должно ронять показ самого ШУ (см. CabinetService.get)
    sim: SimInfoOut | None = None
    tags: list[TagOut] = []
    project_id: int | None
    project_name: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CabinetListOut(BaseModel):
    id: int
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
    project_id: int | None
    project_name: str | None


class UserCabinetPatchIn(BaseModel):
    custom_name: str | None = Field(None, min_length=1, max_length=200)
    custom_comment: str | None = Field(None, max_length=2000)


class AddByPhotoIn(BaseModel):
    # Проект, в котором заявителю не хватает этого ШУ — заявитель должен уже
    # состоять в нём (см. CabinetRequestService.create_addition). Это не "добавь
    # меня к неизвестному ШУ", а "в моём проекте не хватает шкафа, вот фото".
    project_id: int = Field(..., gt=0)
    photo_url: str = Field(..., min_length=1, max_length=500)
    user_comment: str | None = Field(None, min_length=1, max_length=1000)

    # Клиент возвращает подписанный URL, полученный от POST /upload/attachment —
    # в БД подпись попасть не должна, см. app/core/signed_urls.py
    @field_validator("photo_url")
    @classmethod
    def strip_url_signature(cls, v: str) -> str:
        return strip_signature(v) or v


class AddByPhotoOut(BaseModel):
    request_id: int
    message: str = "Заявка отправлена на рассмотрение"
