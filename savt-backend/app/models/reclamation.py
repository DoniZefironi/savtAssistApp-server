from datetime import date, datetime
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Reclamation(Base):
    """Рекламация — гарантийная/негарантийная претензия по ШУ, линии, ПКИ, ПО
    или документации. Обрабатывается и из админки (roles=admin), и на стороне
    Bitrix24 — изменения приезжают вебхуком, см. reclamation_service."""
    __tablename__ = "reclamations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # кто подал
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Статусы соответствуют стадиям смарт-процесса Bitrix один к одному —
    # отдельного поля под стадию нет намеренно: пока "Новая рекламация" и
    # "На рассмотрении" схлопывались в один review, обратная синхронизация была
    # принципиально неполной (из Bitrix уже не восстановить, какая из двух).
    # Карту статус<->стадия держит bitrix_service._RECLAMATION_STATUS_TO_STAGE:
    #   new         Новая рекламация
    #   review      На рассмотрении
    #   in_progress Принята в работу
    #   resolved    Закрыта
    #   rejected    Отклонена
    #   invalid     Ошибочные рекламации
    status: Mapped[str] = mapped_column(String(20), server_default="new", index=True)
    # гарантийный случай или нет — отдельный флаг, а не часть статуса (как
    # ServiceRequest.is_under_warranty), проставляется вместе с переходом в
    # in_progress; до этого момента null — классификация ещё не решена
    warranty_classification: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # id созданного элемента в битрикс
    bitrix_item_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Момент, когда мы узнали, что карточку в Bitrix удалили — из события
    # ONCRMDYNAMICITEMDELETE либо из ответа NOT_FOUND при очередной отправке.
    # Вместе с этим обнуляется bitrix_item_id, поэтому отличить "никогда не
    # уезжала в Bitrix" от "уезжала, но карточку удалили" можно только по
    # этому полю. Заявку при этом НЕ удаляем и заново НЕ заводим: карточку
    # удалили осознанно, а претензия заявителя никуда не делась — решение,
    # что с ней делать, за админом (см. GET /admin/reclamations/bitrix-detached)
    bitrix_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- объект рекламации ---
    # cabinet | line | component | software | documentation, см. CHECK ниже
    object_type: Mapped[str] = mapped_column(String(20), index=True)
    # Ровно одно из cabinet_id/project_id заполнено (см. CHECK ниже, тот же
    # паттерн, что у ServiceRequest) — cabinet_id при object_type=="cabinet"
    # (номер объекта берём через связь Cabinet.object_number, не дублируем
    # здесь), project_id для остальных типов, где нет конкретного ШУ, но
    # рекламация всё равно относится к какому-то проекту/поставке.
    #
    # project_id обязателен для всех типов не просто для порядка: Bitrix
    # тянет компанию-заказчика ("Клиент") и контакты из сделки проекта, и с
    # 2026-09-25 это поле стало обязательным при создании элемента —
    # без deal_id/company_id (см. bitrix_service.create_reclamation_item)
    # crm.item.add падает 400 CRM_FIELD_ERROR_REQUIRED. У рекламаций без
    # project_id (типы line/component/software/documentation до этой правки)
    # взять компанию было неоткуда.
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
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
    # подтверждающий документ — обязателен при закрытии (resolved), см.
    # ReclamationService._check_transition. Загружается через тот же общий
    # POST /upload/attachment, что и остальные вложения; при закрытии
    # отправляется в Bitrix (ufCrm53_1784725447065 "Подтверждающий документ" —
    # там это обязательное поле именно на стадии SUCCESS, проверено вживую)
    confirmation_file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confirmation_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Срок, к которому рекламацию обязуются отработать. Заводится админом и
    # синхронизируется с полем "Дедлайн" карточки Bitrix (ufCrm53_1784791589794,
    # тип date — без времени, отсюда Date, а не DateTime) в обе стороны.
    # Bitrix требует это поле заполненным при переводе карточки между стадиями,
    # так что без него смена статуса в Bitrix не проходит — см.
    # bitrix_service.update_reclamation_stage
    deadline_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ФИО и рабочий телефон ответственного — показываются пользователю в
    # уведомлении о переходе в работу (п.7 ТЗ)
    responsible_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    responsible_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # ID пользователя Bitrix, назначенного ответственным (assignedById).
    # Раньше это поле нигде не хранилось — только "прокидывалось" в Bitrix и
    # забывалось (см. AdminReclamationUpdateIn.responsible_bitrix_user_id),
    # из-за чего было невозможно ни надёжно предвыбрать текущего ответственного
    # в дропдауне админки (сверка по одному только ФИО ненадёжна — тёзки,
    # смена фамилии), ни узнать, что назначение сменили прямо в Bitrix, минуя
    # нашу админку. Обновляется и когда назначаем мы, и по вебхуку — см.
    # reclamation_service.sync_reclamation_from_bitrix
    responsible_bitrix_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
            "status IN ('new', 'review', 'in_progress', 'resolved', 'rejected', 'invalid')",
            name="ck_reclamation_status",
        ),
        # На проде уже есть рекламации без обоих полей (до этой правки
        # project_id не существовал) — в миграции констрейнт добавляется как
        # NOT VALID, чтобы не упасть на старых данных, здесь же описан как
        # обычный CHECK для свежих БД (create_all там данных ещё нет)
        CheckConstraint(
            "(cabinet_id IS NOT NULL) != (project_id IS NOT NULL)",
            name="ck_reclamation_cabinet_or_project",
        ),
    )
