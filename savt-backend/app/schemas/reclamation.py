from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class ReclamationAttachmentIn(BaseModel):
    """Файл уже загружен через POST /upload/attachment — сюда передаётся
    полученный подписанный URL и метаданные из ответа на загрузку."""
    file_url: str = Field(..., max_length=500)
    file_name: str | None = Field(None, max_length=255)
    file_size_bytes: int | None = Field(None, ge=0)
    mime_type: str | None = Field(None, max_length=100)


class ReclamationAttachmentOut(BaseModel):
    id: int
    file_url: str
    file_name: str | None
    file_size_bytes: int | None
    mime_type: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReclamationCreateIn(BaseModel):
    object_type: str = Field(..., pattern="^(cabinet|line|component|software|documentation)$")
    # обязателен, только если object_type == "cabinet" — проверяется в сервисе,
    # т.к. правило зависит от object_type, а не от самого поля
    cabinet_id: int | None = Field(None, gt=0)
    # состав зависит от object_type: line -> {"serial_number": "..."},
    # component -> {"name", "model", "article", "serial_number"} (п.4 ТЗ),
    # software/documentation -> что понадобится по факту
    object_details: dict[str, Any] | None = None

    contract_number: str | None = Field(None, max_length=100)
    order_number: str | None = Field(None, max_length=100)
    ttn_number: str | None = Field(None, max_length=100)

    description: str = Field(..., min_length=1)
    occurrence_conditions: str | None = None
    error_codes: str | None = None

    # предзаполняются на клиенте из профиля, но редактируемые — заявка может
    # подаваться не от своего имени (см. customer_name)
    contact_name: str = Field(..., min_length=1, max_length=200)
    contact_phone: str = Field(..., min_length=1, max_length=20)
    contact_email: str = Field(..., min_length=1, max_length=100)
    customer_name: str | None = Field(None, max_length=255)

    attachments: list[ReclamationAttachmentIn] = []


class ReclamationListItemOut(BaseModel):
    id: int
    object_type: str
    status: str
    warranty_classification: bool | None
    description: str
    # для отображения заголовка карточки в списке, когда object_type == cabinet —
    # без этого клиенту пришлось бы отдельно тянуть GET /cabinets/{id}
    cabinet_object_number: str | None = None
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class ReclamationDetailOut(BaseModel):
    id: int
    status: str
    warranty_classification: bool | None

    object_type: str
    cabinet_id: int | None
    cabinet_object_number: str | None = None
    object_details: dict[str, Any] | None

    contract_number: str | None
    order_number: str | None
    ttn_number: str | None

    description: str
    occurrence_conditions: str | None
    error_codes: str | None

    contact_name: str
    contact_phone: str
    contact_email: str
    customer_name: str | None

    root_cause: str | None
    resolution_comment: str | None
    rejection_reason: str | None
    responsible_name: str | None
    responsible_phone: str | None
    confirmation_file_url: str | None
    confirmation_file_name: str | None

    created_at: datetime
    resolved_at: datetime | None

    attachments: list[ReclamationAttachmentOut] = []

    model_config = {"from_attributes": True}


# --- админка: пока без Битрикса роль "закреплённых специалистов" временно
# исполняет админ вручную (см. Reclamation.__doc__ в app/models/reclamation.py) ---

class AdminReclamationListItemOut(ReclamationListItemOut):
    user_id: int
    # ФИО подавшего аккаунта — не путать с contact_name (снимок с формы заявки,
    # может отличаться, если подано не от своего имени)
    user_full_name: str | None = None
    # Название стадии карточки в Bitrix — только для админки; стадий там шесть,
    # а наших статусов четыре, часть стадий схлопывается в один наш статус
    # (см. Reclamation.bitrix_stage_id). null — рекламация ещё не доехала до
    # Bitrix либо стоит на стадии, которой нет в нашем справочнике
    bitrix_stage_name: str | None = None


class AdminReclamationOut(ReclamationDetailOut):
    user_id: int
    user_full_name: str | None = None
    # см. AdminReclamationListItemOut.bitrix_stage_name; в карточке отдаём ещё
    # и сырой код стадии с id элемента — чтобы администратор интеграции мог
    # сопоставить с самим Bitrix, когда что-то разъезжается
    bitrix_item_id: str | None = None
    bitrix_stage_id: str | None = None
    bitrix_stage_name: str | None = None


class AdminReclamationUpdateIn(BaseModel):
    """Частичное обновление (exclude_unset). Проверки вроде "нельзя отклонить
    без причины" — в сервисе при смене статуса, не здесь: правило зависит от
    итогового состояния объекта (какой статус ставится), а не от одного поля."""
    status: str | None = Field(None, pattern="^(review|in_progress|resolved|rejected)$")
    warranty_classification: bool | None = None
    root_cause: str | None = None
    resolution_comment: str | None = None
    rejection_reason: str | None = None
    responsible_name: str | None = Field(None, max_length=200)
    responsible_phone: str | None = Field(None, max_length=20)
    # подтверждающий документ — загружается заранее через POST /upload/attachment,
    # сюда передаётся уже готовая ссылка; обязателен при переходе в resolved
    # (см. ReclamationService._check_transition)
    confirmation_file_url: str | None = Field(None, max_length=500)
    confirmation_file_name: str | None = Field(None, max_length=255)
    # ID пользователя Bitrix, выбранного в дропдауне (GET /admin/reclamations/
    # bitrix-users) — не хранится у нас в БД, используется только чтобы
    # пробросить assignedById в саму карточку Bitrix (см. ReclamationService.update).
    # responsible_name/responsible_phone по-прежнему передавайте отдельно —
    # это поле их не заменяет, только дополнительно синхронизирует с Bitrix
    responsible_bitrix_user_id: int | None = None
    # Стадия карточки в Bitrix — нужна только чтобы отличить "Новая рекламация"
    # от "На рассмотрении": обе = наш review, через status разницу не выразить.
    # Допустимы ровно эти две стадии и только пока рекламация в review —
    # остальные стадии двигаются сменой status, см. ReclamationService.update
    bitrix_stage_id: str | None = Field(None, max_length=50)


class BitrixUserOut(BaseModel):
    id: int
    full_name: str
    phone: str | None
    position: str | None


class ReclamationOutboxOut(BaseModel):
    """Недоставленная попытка синхронизации с Bitrix (п.8 ТЗ) — для
    администратора интеграции, см. GET /admin/reclamations/bitrix-outbox."""
    id: int
    reclamation_id: int
    operation: str
    attempts: int
    last_error: str | None
    created_at: datetime
    last_attempted_at: datetime | None

    model_config = {"from_attributes": True}
