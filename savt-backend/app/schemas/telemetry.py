from datetime import datetime
from pydantic import BaseModel, Field


# Входящий вебхук от C#-прокси. registers — {адрес: значение}, как есть с ПЛК.
# timestamp необязателен: если прокси не прислал время фактического чтения,
# берём время получения на сервере.
class TelemetryWebhookIn(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    registers: dict[int, int]
    timestamp: datetime | None = None


# Значение регистра — 16-битное слово, каждый бит здесь своя (потенциальная)
# авария. Название берём из CabinetRegisterOverride (если есть для этого ШУ и
# пары адрес+бит), иначе из RegisterDefinition; null - бит не описан ни там,
# ни там. value — состояние САМОГО БИТА (0 или 1), не сырое число регистра
class TelemetryRegisterOut(BaseModel):
    address: int
    bit: int
    name: str | None
    value: int


class TelemetryEventOut(BaseModel):
    id: int
    received_at: datetime
    registers: list[TelemetryRegisterOut]


# "Текущее состояние" — то, что реально отдаёт GET /cabinets/{id}/telemetry.
# У каждого бита своё updated_at (наследуется от updated_at всего регистра —
# разные регистры меняются с разной частотой), в отличие от TelemetryEventOut,
# где все биты одного сообщения делят один received_at — то была история
# сообщений, это снимок "что сейчас"
class TelemetryCurrentRegisterOut(BaseModel):
    address: int
    bit: int
    name: str | None
    value: int
    updated_at: datetime


class TelemetryCurrentStateOut(BaseModel):
    registers: list[TelemetryCurrentRegisterOut]


# Один элемент ответа GET /webhooks/telemetry/targets — "куда подключаться и что
# слушать" для конкретного ШУ. Отдаётся только прокси (по секрету), не пользователю
class TelemetryTargetOut(BaseModel):
    cabinet_id: int
    host: str
    port: int
    topic: str
    username: str | None
    password: str | None


# Стандартная карта регистров (админ, общая для всех ШУ)
class RegisterDefinitionIn(BaseModel):
    address: int = Field(..., ge=0, le=65534)
    bit: int = Field(..., ge=0, le=15)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class RegisterDefinitionOut(BaseModel):
    id: int
    address: int
    bit: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# Добавка/переопределение карты на конкретный ШУ (админ)
class CabinetRegisterOverrideIn(BaseModel):
    address: int = Field(..., ge=0, le=65534)
    bit: int = Field(..., ge=0, le=15)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class CabinetRegisterOverrideOut(BaseModel):
    id: int
    cabinet_id: int
    address: int
    bit: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
