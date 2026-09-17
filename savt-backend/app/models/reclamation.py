from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Reclamation(Base):
    """Рекламация — гарантийная/негарантийная претензия по ШУ, линии, ПКИ, ПО
    или документации. Пока без интеграции с Битрикс24 — обработка (смена
    статуса, классификация, назначение ответственного) временно делается
    вручную из админки, roles=admin; когда подключится Битрикс, источник этих
    же полей сменится с ручного PATCH на вебхук, модель данных не изменится."""
    __tablename__ = "reclamations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # кто подал
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # статус: review (на рассмотрении) -> in_progress (в работе) -> resolved/rejected
    status: Mapped[str] = mapped_column(String(20), server_default="review", index=True)
    # гарантийный случай или нет — отдельный флаг, а не часть статуса (как
    # ServiceRequest.is_under_warranty), проставляется вместе с переходом в
    # in_progress; до этого момента null — классификация ещё не решена
    warranty_classification: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # id созданного элемента в битрикс
    bitrix_item_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # --- объект рекламации ---
    # cabinet | line | component | software | documentation, см. CHECK ниже
    object_type: Mapped[str] = mapped_column(String(20), index=True)
    # заполнен только если object_type == cabinet — номер объекта берём через
    # связь (Cabinet.object_number), не дублируем в этой таблице
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), nullable=True, index=True)
    # для остальных типов объекта — набор полей разный (у line просто
    # serial_number, у component — наименование/модель/артикул/серийный номер
    # из п.4 ТЗ), поэтому JSONB вместо кучи специфичных nullable-колонок,
    # из которых на любую конкретную заявку заполнена максимум одна группа
    object_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # № договора/заказа/ТТН-CMR — при наличии, не зависят от типа объекта
    contract_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    order_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ttn_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # --- описание неисправности ---
    description: Mapped[str] = mapped_column(Text)
    occurrence_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_codes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- контакты: снимок на момент подачи, не ссылка на профиль — иначе более
    # поздняя правка User.phone/email задним числом поменяла бы данные уже
    # закрытой заявки. Предзаполняются из профиля, редактируются перед отправкой. ---
    contact_name: Mapped[str] = mapped_column(String(200))
    contact_phone: Mapped[str] = mapped_column(String(20))
    contact_email: Mapped[str] = mapped_column(String(100))
    # заявитель/заказчик — если заявка подаётся не от своего имени
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- заполняется после подачи, при обработке ---
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ФИО и рабочий телефон ответственного — показываются пользователю в
    # уведомлении о переходе в работу (п.7 ТЗ)
    responsible_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    responsible_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Reclamation id={self.id} object_type={self.object_type} status={self.status}>"

    __table_args__ = (
        CheckConstraint(
            "object_type IN ('cabinet', 'line', 'component', 'software', 'documentation')",
            name="ck_reclamation_object_type",
        ),
        CheckConstraint(
            "status IN ('review', 'in_progress', 'resolved', 'rejected')",
            name="ck_reclamation_status",
        ),
    )
