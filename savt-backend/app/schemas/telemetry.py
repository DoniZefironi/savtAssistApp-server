from datetime import datetime
from pydantic import BaseModel, Field


# Входящий вебхук от C#-прокси. registers — {адрес: значение}, как есть с ПЛК.
# timestamp необязателен: если прокси не прислал время фактического чтения,
# берём время получения на сервере.
class TelemetryWebhookIn(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    registers: dict[int, int]
    timestamp: datetime | None = None


class TelemetryRegisterOut(BaseModel):
    address: int
    # NULL — весь регистр одно значение; 0-15 — конкретный бит внутри него
    # (регистр трактуется как битовая маска, если на его адрес заведён хотя бы
    # один bit-уровневый RegisterDefinition/CabinetRegisterOverride)
    bit: int | None
    # Название берём из CabinetRegisterOverride (если есть для этого ШУ,
    # адреса и bit), иначе из RegisterDefinition; null - не описано ни там, ни там
    name: str | None
    # Сырое значение регистра целиком (одно и то же для всех bit-строк одного адреса)
    value: int
    # Для bit-строк — установлен ли этот конкретный бит в value; null для
    # not-bit строк (bit=NULL), там смысл несёт value целиком, а не флаг
    active: bool | None


class TelemetryEventOut(BaseModel):
    id: int
    received_at: datetime
    registers: list[TelemetryRegisterOut]


# Один элемент ответа GET /webhooks/telemetry/targets — "куда подключаться и что
# слушать" для конкретного ШУ. Отдаётся только прокси (по секрету), не пользователю
class TelemetryTargetOut(BaseModel):
    cabinet_id: int
    host: str
    port: int
    topic: str
    username: str | None
    password: str | None


# Стандартная карта регистров (админ, общая для всех ШУ). bit=None — обычный
# регистр с одним значением ("Температура насоса"); bit=0..15 — конкретный бит
# внутри 16-битного слова (типовая ПЛК-таблица "Неисправности": один регистр
# аварий, где у каждого бита своя названная неисправность)
class RegisterDefinitionIn(BaseModel):
    address: int = Field(..., ge=0, le=65534)
    bit: int | None = Field(None, ge=0, le=15)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class RegisterDefinitionOut(BaseModel):
    id: int
    address: int
    bit: int | None
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# Добавка/переопределение карты на конкретный ШУ (админ)
class CabinetRegisterOverrideIn(BaseModel):
    address: int = Field(..., ge=0, le=65534)
    bit: int | None = Field(None, ge=0, le=15)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class CabinetRegisterOverrideOut(BaseModel):
    id: int
    cabinet_id: int
    address: int
    bit: int | None
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
