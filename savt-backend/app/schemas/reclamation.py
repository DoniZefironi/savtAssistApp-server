from datetime import date, datetime
from typing import Any
from pydantic import BaseModel, Field, field_validator

from app.core.signed_urls import SignedUrl, SignedUrlOpt, strip_signature


class ReclamationAttachmentIn(BaseModel):
    """Файл уже загружен через POST /upload/attachment — сюда передаётся
    полученный подписанный URL и метаданные из ответа на загрузку.

    Клиент присылает обратно тот URL, который получил от нас, то есть уже
    подписанный — в БД подпись попасть не должна (протухнет вместе с записью,
    см. app/core/signed_urls.py), снимаем её здесь же, как и везде, где
    принимается URL файла (ChatAttachmentIn, WallpaperIn и т.п.)."""
    file_url: str = Field(..., max_length=500)
    file_name: str | None = Field(None, max_length=255)
    file_size_bytes: int | None = Field(None, ge=0)
    mime_type: str | None = Field(None, max_length=100)

    @field_validator("file_url")
    @classmethod
    def strip_url_signature(cls, v: str) -> str:
        return strip_signature(v) or v


class ReclamationAttachmentOut(BaseModel):
    id: int
    file_url: SignedUrl
    file_name: str | None
    file_size_bytes: int | None
    mime_type: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReclamationCreateIn(BaseModel):
    object_type: str = Field(..., pattern="^(cabinet|line|component|software|documentation)$")
    # состав зависит от object_type: cabinet/line -> {"serial_number": "..."}
    # (заводской номер пользователь списывает вручную с самого объекта),
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
    # то же самое для остальных object_type — там вместо ШУ привязка к проекту
    project_name: str | None = None
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
    project_id: int | None
    project_name: str | None = None
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
    confirmation_file_url: SignedUrlOpt
    confirmation_file_name: str | None

    created_at: datetime
    resolved_at: datetime | None

    attachments: list[ReclamationAttachmentOut] = []

    model_config = {"from_attributes": True}


# --- админка: обработка идёт и отсюда, и на стороне Bitrix — статусы
# синхронизируются в обе стороны (см. Reclamation.__doc__) ---

class ReclamationOutboxOut(BaseModel):
    """Недоставленная попытка синхронизации с Bitrix (п.8 ТЗ) — для
    администратора интеграции, см. GET /admin/reclamations/bitrix-outbox.
    payload виден полностью — по нему понятно, что именно не отправилось
    (например, пустой company_id при сбое create), без захода в БД руками."""
    id: int
    reclamation_id: int
    operation: str
    payload: dict[str, Any]
    attempts: int
    last_error: str | None
    created_at: datetime
    last_attempted_at: datetime | None

    model_config = {"from_attributes": True}


class AdminReclamationListItemOut(ReclamationListItemOut):
    user_id: int
    # ФИО подавшего аккаунта — не путать с contact_name (снимок с формы заявки,
    # может отличаться, если подано не от своего имени)
    user_full_name: str | None = None
    # срок отработки, синхронизируется с "Дедлайном" карточки Bitrix
    deadline_at: date | None = None


class AdminReclamationOut(ReclamationDetailOut):
    user_id: int
    user_full_name: str | None = None
    deadline_at: date | None = None
    # ID ответственного в Bitrix — чтобы дропдаун выбора ответственного
    # (GET /admin/reclamations/bitrix-users) мог предвыбрать текущее значение
    # по ID, а не гадать по совпадению ФИО. Синхронизируется в обе стороны,
    # как и deadline_at — см. Reclamation.responsible_bitrix_user_id
    responsible_bitrix_user_id: int | None = None
    # id карточки на портале — чтобы администратор интеграции мог сопоставить
    # с самим Bitrix, когда что-то разъезжается
    bitrix_item_id: str | None = None
    # заполнено, если карточку в Bitrix удалили: тогда bitrix_item_id пуст, и
    # отличить это от "никогда не уезжала в Bitrix" можно только отсюда
    bitrix_deleted_at: datetime | None = None
    pending_create_outbox: ReclamationOutboxOut | None = None


class BitrixUserOut(BaseModel):
    id: int
    full_name: str
    phone: str | None
    position: str | None


class ReclamationDetachedOut(BaseModel):
    """Рекламация, карточку которой удалили в Bitrix. Заявка осталась у нас,
    но с порталом больше не связана — заводить её заново или закрывать,
    решает админ. См. GET /admin/reclamations/bitrix-detached."""
    id: int
    status: str
    description: str
    user_full_name: str | None = None
    created_at: datetime
    bitrix_deleted_at: datetime

    model_config = {"from_attributes": True}


class ReclamationOutboxUpdateIn(BaseModel):
    """PATCH /admin/reclamations/bitrix-outbox/{id} — ручная правка застрявшего
    payload (например, дописать company_id, которого не было в сделке Bitrix
    на момент сбоя). Произвольный словарь, без строгой схемы под каждую из 6
    операций — это инструмент на крайний случай для администратора, не
    основной путь ввода данных."""
    payload: dict[str, Any]


class ReclamationOutboxRetryResult(BaseModel):
    """Результат ручного повтора после PATCH — пробуем отправить сразу, не
    ждём ближайшего 15-минутного цикла retry_bitrix_outbox, чтобы админ увидел
    результат правки тут же. success=True — операция прошла и строка удалена
    (row=None); success=False — снова не удалось, row содержит новую ошибку."""
    success: bool
    row: ReclamationOutboxOut | None = None
