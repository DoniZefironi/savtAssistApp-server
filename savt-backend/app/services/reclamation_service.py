import logging

from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background import spawn
from app.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.reclamation import Reclamation
from app.repositories.reclamation import ReclamationRepository
from app.schemas.pagination import PageOut, make_page
from app.schemas.reclamation import (
    AdminReclamationListItemOut,
    AdminReclamationOut,
    ReclamationAttachmentOut,
    ReclamationCreateIn,
    ReclamationDetachedOut,
    ReclamationDetailOut,
    ReclamationListItemOut,
    ReclamationOutboxOut,
    ReclamationOutboxRetryResult,
)
from app.services.audit_service import AuditLogger
from app.services.notification_service import NotificationService

_log = logging.getLogger(__name__)


class ReclamationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ReclamationRepository(session)
        self.audit = AuditLogger(session)

    # --- пользователь ---

    async def create(self, user_id: int, data: ReclamationCreateIn) -> ReclamationDetailOut:
        # Заводской номер / данные ПКИ — для "cabinet" не нужны в payload
        # вообще: заводской номер там всегда берётся из cabinet.object_number
        # (обязательная колонка, см. app/models/cabinets.py), пользователь
        # ничего не вводит. А вот для "line"/"component" это ровно те поля,
        # что уходят в нативные UF-поля Bitrix (см. _build_bitrix_native_fields)
        # — раньше были необязательны, из-за чего карточка реально уезжала в
        # Bitrix с пустым "Заводской номер ШУ или линии" / "Данные ПКИ"
        # (обнаружено 2026-09-28 тестовой рекламацией №44)
        if data.object_type in ("cabinet", "line"):
            if not data.object_details or not data.object_details.get("serial_number"):
                raise ValidationError("Нужно указать заводской номер")
        elif data.object_type == "component":
            d = data.object_details or {}
            missing = [
                label for key, label in (
                    ("name", "наименование"), ("model", "модель"),
                    ("article", "артикул"), ("serial_number", "серийный номер"),
                ) if not d.get(key)
            ]
            if missing:
                raise ValidationError(f"Для ПКИ нужно указать: {', '.join(missing)}")

        payload = data.model_dump(exclude={"attachments"})
        rec = await self.repo.create(user_id, payload)
        for att in data.attachments:
            await self.repo.add_attachment(rec.id, att.model_dump())

        self.audit.log(
            "reclamation.create", "reclamation", rec.id, user_id, "user",
            {"object_type": rec.object_type},
        )
        await self.session.commit()

        first_attachment_url = data.attachments[0].file_url if data.attachments else None
        _sync_to_bitrix(
            rec.id, _build_bitrix_description(rec), first_attachment_url,
            rec.object_type, rec.object_details, rec.contract_number, rec.order_number, rec.ttn_number,
        )

        row = await self.repo.get_with_cabinet_for_user(user_id, rec.id)
        return await self._detail_out(*row)

    async def get_for_user(self, user_id: int, reclamation_id: int) -> ReclamationDetailOut:
        row = await self.repo.get_with_cabinet_for_user(user_id, reclamation_id)
        if row is None:
            raise NotFoundError("Рекламация не найдена")
        return await self._detail_out(*row)

    async def list_for_user(
        self, user_id: int, status: str | None, page: int, size: int,
    ) -> PageOut[ReclamationListItemOut]:
        rows, total = await self.repo.list_for_user(user_id, status, (page - 1) * size, size)
        items = [
            ReclamationListItemOut(**self._list_fields(rec, cabinet, project))
            for rec, cabinet, project in rows
        ]
        return make_page(items, total, page, size)

    # --- админка: пока без Битрикса роль "закреплённых специалистов" временно
    # исполняет админ вручную, см. Reclamation.__doc__ ---

    async def list_admin(
        self,
        status: str | None, object_type: str | None, warranty_classification: bool | None,
        page: int, size: int,
        search: str | None = None, sort_by: str = "created_at", sort_order: str = "desc",
    ) -> PageOut[AdminReclamationListItemOut]:
        rows, total = await self.repo.list_admin(
            status, object_type, warranty_classification, search, sort_by, sort_order,
            (page - 1) * size, size,
        )
        items = [
            AdminReclamationListItemOut(
                **self._list_fields(rec, cabinet, project), user_id=user.id, user_full_name=user.full_name,
                deadline_at=rec.deadline_at,
            )
            for rec, user, cabinet, project in rows
        ]
        return make_page(items, total, page, size)

    async def get_admin(self, reclamation_id: int) -> AdminReclamationOut:
        row = await self.repo.get_with_relations(reclamation_id)
        if row is None:
            raise NotFoundError("Рекламация не найдена")
        rec, user, cabinet, project = row
        detail = await self._detail_out(rec, cabinet, project)
        pending_create = None
        if rec.bitrix_item_id is None and rec.bitrix_deleted_at is None:
            from app.repositories.reclamation_outbox import ReclamationOutboxRepository
            row_outbox = await ReclamationOutboxRepository(self.session).get_pending_create(rec.id)
            if row_outbox is not None:
                pending_create = ReclamationOutboxOut.model_validate(row_outbox)
        return AdminReclamationOut(
            **detail.model_dump(), user_id=user.id, user_full_name=user.full_name,
            deadline_at=rec.deadline_at, responsible_bitrix_user_id=rec.responsible_bitrix_user_id,
            bitrix_item_id=rec.bitrix_item_id, bitrix_deleted_at=rec.bitrix_deleted_at,
            pending_create_outbox=pending_create,
        )


    async def delete_detached(self, reclamation_id: int, actor_id: int, actor_role: str) -> None:
        """Удаление — только для рекламаций, чью карточку уже удалили в Bitrix.
        Живую удалять нельзя: у неё осталась бы карточка на портале без пары
        у нас, и вебхуки по ней молча уходили бы в никуда. Вложения и очередь
        повторов удаляются каскадом (ondelete=CASCADE в схеме)."""
        rec = await self.repo.get_by_id(reclamation_id)
        if rec is None:
            raise NotFoundError("Рекламация не найдена")
        if rec.bitrix_deleted_at is None:
            raise ValidationError(
                "Удалить можно только рекламацию, чью карточку уже удалили в Bitrix"
            )

        self.audit.log(
            "reclamation.delete", "reclamation", rec.id, actor_id, actor_role,
            {"status": rec.status, "user_id": rec.user_id},
        )
        await self.session.delete(rec)
        await self.session.commit()

    # Для "администратора интеграции" из ТЗ (п.3 — "обрабатывает ошибки
    # интеграции") — что сейчас не долетело до Bitrix и почему
    async def list_outbox(self) -> list[ReclamationOutboxOut]:
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        rows = await ReclamationOutboxRepository(self.session).list_pending()
        return [ReclamationOutboxOut.model_validate(r) for r in rows]

    # Ручное вмешательство администратора интеграции в застрявшую операцию
    # (см. историю с рекламацией №30 — company_id в сделке Bitrix отсутствовал,
    # а поправить payload или просто снять операцию с повторов было нельзя,
    # только руками в БД). retry_outbox_now пробует отправить сразу с новыми
    # данными, не дожидаясь ближайшего 15-минутного цикла retry_bitrix_outbox.
    async def retry_outbox_now(
        self, outbox_id: int, payload: dict,
    ) -> ReclamationOutboxRetryResult | None:
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository

        repo = ReclamationOutboxRepository(self.session)
        row = await repo.get(outbox_id)
        if row is None:
            return None
        row.payload = payload
        await self.session.flush()
        success = await _retry_outbox_row(self.session, repo, row)
        await self.session.commit()
        if success:
            return ReclamationOutboxRetryResult(success=True)
        return ReclamationOutboxRetryResult(success=False, row=ReclamationOutboxOut.model_validate(row))

    async def delete_outbox(self, outbox_id: int) -> bool:
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        repo = ReclamationOutboxRepository(self.session)
        row = await repo.get(outbox_id)
        if row is None:
            return False
        await repo.delete(row)
        await self.session.commit()
        return True

    # Рекламации, чью карточку удалили в Bitrix — туда же, к администратору
    # интеграции: заявка у нас живая, но с порталом больше не связана
    async def list_detached(self) -> list[ReclamationDetachedOut]:
        rows = await self.repo.list_bitrix_detached()
        return [
            ReclamationDetachedOut(
                id=rec.id, status=rec.status, description=rec.description,
                user_full_name=user.full_name if user else None,
                created_at=rec.created_at, bitrix_deleted_at=rec.bitrix_deleted_at,
            )
            for rec, user in rows
        ]

    async def _notify_status_change(self, rec) -> None:
        if rec.status == "in_progress":
            # Классификация больше не обязательна к этому моменту (снято
            # 2026-09-25) — warranty_classification может быть ещё null,
            # и это не то же самое, что "не гарантия": заказчику нельзя
            # молча сказать "платно" раньше, чем это реально решили
            if rec.warranty_classification is None:
                label = "В работе"
            elif rec.warranty_classification:
                label = "В работе. Гарантия"
            else:
                label = "В работе. Не гарантия"
            body = f"Статус изменён: «{label}»"
            if rec.responsible_name:
                body += f". Ответственный: {rec.responsible_name}"
                if rec.responsible_phone:
                    body += f", тел. {rec.responsible_phone}"
        elif rec.status == "review":
            body = "Рекламация принята на рассмотрение"
        # Причину и итог заполняют только в Bitrix, у большинства рекламаций они
        # пусты — пустое в текст не подставляем
        elif rec.status == "rejected":
            body = "Рекламация отклонена" + (f". Причина: {rec.rejection_reason}" if rec.rejection_reason else "")
        elif rec.status == "invalid":
            body = "Рекламация оформлена некорректно" + (
                f". Причина: {rec.rejection_reason}" if rec.rejection_reason else ""
            )
        elif rec.status == "resolved":
            body = "Рекламация исполнена" + (f". {rec.resolution_comment}" if rec.resolution_comment else "")
        else:
            # new — начальный статус, заявитель только что подал её сам
            return
        await NotificationService(self.session).send(
            user_id=rec.user_id, type_="request_status",
            title="Рекламация", body=body,
            data={"reclamation_id": rec.id, "status": rec.status},
        )

    # --- сборка ответов ---

    @staticmethod
    def _list_fields(rec, cabinet, project=None) -> dict:
        return dict(
            id=rec.id, object_type=rec.object_type, status=rec.status,
            warranty_classification=rec.warranty_classification,
            description=rec.description,
            cabinet_object_number=cabinet.object_number if cabinet else None,
            project_name=project.name if project else None,
            created_at=rec.created_at, resolved_at=rec.resolved_at,
        )

    async def _detail_out(self, rec, cabinet, project=None) -> ReclamationDetailOut:
        attachments = await self.repo.list_attachments(rec.id)
        return ReclamationDetailOut(
            id=rec.id, status=rec.status, warranty_classification=rec.warranty_classification,
            object_type=rec.object_type, cabinet_id=rec.cabinet_id,
            cabinet_object_number=cabinet.object_number if cabinet else None,
            project_id=rec.project_id, project_name=project.name if project else None,
            object_details=rec.object_details,
            contract_number=rec.contract_number, order_number=rec.order_number, ttn_number=rec.ttn_number,
            description=rec.description, occurrence_conditions=rec.occurrence_conditions,
            error_codes=rec.error_codes,
            contact_name=rec.contact_name, contact_phone=rec.contact_phone, contact_email=rec.contact_email,
            customer_name=rec.customer_name,
            root_cause=rec.root_cause, resolution_comment=rec.resolution_comment,
            rejection_reason=rec.rejection_reason,
            responsible_name=rec.responsible_name, responsible_phone=rec.responsible_phone,
            confirmation_file_url=rec.confirmation_file_url, confirmation_file_name=rec.confirmation_file_name,
            created_at=rec.created_at, resolved_at=rec.resolved_at,
            attachments=[ReclamationAttachmentOut.model_validate(a) for a in attachments],
        )


# Модульные функции, не методы: _sync_to_bitrix запускается через
# spawn (app/core/background.py) в своей собственной сессии, к моменту её реального
# выполнения request-сессия (self.session) может быть уже закрыта — как и у
# ServiceRequestService._sync_to_bitrix, см. app/services/service_request_service.py

def _build_bitrix_native_fields(
    object_type: str, object_details: dict | None,
    contract_number: str | None, order_number: str | None, ttn_number: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Собирает значения для трёх новых нативных полей процесса (появились
    2026-09-23) — заводской номер, № договора/заказа/ТТН, данные ПКИ.
    Возвращает (object_serial_number, contract_info, component_info)."""
    object_serial_number = component_info = None
    if object_type in ("cabinet", "line") and object_details:
        object_serial_number = object_details.get("serial_number")
    elif object_type == "component" and object_details:
        d = object_details
        component_info = ", ".join(
            f"{label}: {value}" for label, value in (
                ("наименование", d.get("name")), ("модель", d.get("model")),
                ("артикул", d.get("article")), ("серийный номер", d.get("serial_number")),
            ) if value
        ) or None

    contract_info = ", ".join(
        part for part in (
            f"Договор: {contract_number}" if contract_number else None,
            f"Заказ: {order_number}" if order_number else None,
            f"ТТН/CMR: {ttn_number}" if ttn_number else None,
        ) if part
    ) or None

    return object_serial_number, contract_info, component_info


def _sync_to_bitrix(
    reclamation_id: int, description: str, attachment_url: str | None,
    object_type: str, object_details: dict | None,
    contract_number: str | None, order_number: str | None, ttn_number: str | None,
) -> None:
    async def _task():
        from app.database import AsyncSessionLocal
        from app.services import bitrix_service

        async with AsyncSessionLocal() as session:
            object_serial_number, contract_info, component_info = _build_bitrix_native_fields(
                object_type, object_details, contract_number, order_number, ttn_number,
            )

            try:
                from app.core.signed_urls import sign_url
                item_id = await bitrix_service.create_reclamation_item(
                    description, None, None, sign_url(attachment_url), None,
                    object_serial_number, contract_info, component_info,
                )
                if not item_id:
                    return
            except Exception as exc:
                _log.exception("Bitrix item creation failed for reclamation %s", reclamation_id)
                # в очередь повторов + уведомление админам — рекламация не
                # доехала до Bitrix, и повторно отправить её может только админ
                await _record_bitrix_failure(
                    reclamation_id, "create",
                    {
                        "description": description, "attachment_url": attachment_url,
                        "object_serial_number": object_serial_number, "contract_info": contract_info,
                        "component_info": component_info,
                    },
                    exc,
                )
                return

            rec = await session.get(Reclamation, reclamation_id)
            if rec is not None:
                rec.bitrix_item_id = item_id
                await session.commit()

    spawn(_task())


async def _notify_integration_admins(session, title: str, body: str, data: dict) -> None:
    """п.8 ТЗ: 'при ошибке передачи ... уведомить администратора интеграции' —
    раньше этого не было вообще, узнать о сбое можно было только зайдя
    вручную в GET /admin/reclamations/bitrix-outbox (так реально копились
    незамеченные сбои — см. историю с рекламацией №16). Получатели — те же,
    кому вообще доступны ручки /admin/reclamations (require_role(ADMIN) даёт
    admin+superadmin, см. app/core/dependencies._ROLE_HIERARCHY), operator
    к обработке рекламаций доступа не имеет и сюда не входит."""
    from sqlalchemy import select
    from app.models.role import Role
    from app.models.user import User

    admins = (await session.execute(
        select(User.id)
        .join(Role, Role.id == User.role_id)
        .where(Role.name.in_(["admin", "superadmin"]), User.is_active == True)
    )).scalars().all()

    for admin_id in admins:
        await NotificationService(session).send(
            user_id=admin_id, type_="bitrix_sync_error",
            title=title, body=body, data=data,
        )


async def _record_bitrix_failure(
    reclamation_id: int, operation: str, payload: dict, exc: Exception,
) -> None:
    """Куда девать сбой отправки. Если карточку удалили — отвязываем
    рекламацию: повторять нечего и некуда. Всё остальное считаем временным и
    кладём в очередь повторов. Уведомление администраторам — только на этот,
    первый сбой конкретной операции, а не на каждую последующую попытку
    повтора: retry_bitrix_outbox сам её не поднимает (у него отдельная ветка
    обработки ошибок, mark_failed_attempt), поэтому спама на каждые 15 минут
    не будет, даже если сбой не устраняется долго."""
    from app.database import AsyncSessionLocal
    from app.repositories.reclamation_outbox import ReclamationOutboxRepository

    async with AsyncSessionLocal() as session:
        rec = await session.get(Reclamation, reclamation_id)
        if rec is not None and rec.bitrix_item_id and _is_item_gone(str(exc)):
            await mark_bitrix_item_deleted(session, rec, f"NOT_FOUND при отправке ({operation})")
        else:
            await ReclamationOutboxRepository(session).create(
                reclamation_id, operation, payload, str(exc),
            )
            await _notify_integration_admins(
                session,
                title="Сбой синхронизации с Bitrix",
                body=f"Рекламация №{reclamation_id}: не удалось отправить «{operation}». {str(exc)[:200]}",
                data={"reclamation_id": reclamation_id, "operation": operation},
            )
        await session.commit()


def _is_item_gone(error_text: str) -> bool:
    """Ответ Bitrix про удалённую карточку. Отличать важно: обычный сбой имеет
    смысл повторять, а удаление — неустранимо, и повторы будут долбиться
    вечно (реально накопилось 10 попыток, прежде чем это заметили)."""
    return "NOT_FOUND" in error_text


async def mark_bitrix_item_deleted(session, rec, reason: str) -> None:
    """Карточки в Bitrix больше нет: отвязываем рекламацию и снимаем с неё все
    недоставленные операции — отправлять их некуда.

    Саму заявку не трогаем и заново в Bitrix не заводим: карточку удалили
    осознанно, а претензия заявителя никуда не делась. Что с ней делать,
    решает админ, см. GET /admin/reclamations/bitrix-detached — туда же и
    уведомление ниже: вызывается из трёх мест (сбой отправки, вебхук
    удаления, повтор из очереди), проще уведомить один раз здесь, чем в
    каждом месте отдельно."""
    from sqlalchemy import delete
    from app.models.reclamation_bitrix_outbox import ReclamationBitrixOutbox

    _log.warning(
        "Рекламация %s: карточка Bitrix %s удалена (%s) — отвязываю",
        rec.id, rec.bitrix_item_id, reason,
    )
    rec.bitrix_item_id = None
    rec.bitrix_deleted_at = datetime.now(timezone.utc)
    await session.execute(
        delete(ReclamationBitrixOutbox).where(
            ReclamationBitrixOutbox.reclamation_id == rec.id
        )
    )
    await _notify_integration_admins(
        session,
        title="Карточка рекламации удалена в Bitrix",
        body=f"Рекламация №{rec.id} отвязана от Bitrix ({reason}). Решите, заводить заново или закрывать.",
        data={"reclamation_id": rec.id},
    )


async def handle_bitrix_item_deleted(item_id: str) -> None:
    """Событие ONCRMDYNAMICITEMDELETE — карточку удалили прямо в Bitrix.
    Узнаём сразу, не дожидаясь, пока очередная отправка упрётся в NOT_FOUND."""
    from app.database import AsyncSessionLocal
    from app.repositories.reclamation import ReclamationRepository

    async with AsyncSessionLocal() as session:
        rec = await ReclamationRepository(session).find_by_bitrix_item_id(item_id)
        if rec is None:
            _log.info(
                "Bitrix reclamation delete: элемент %s не привязан ни к одной рекламации", item_id,
            )
            return
        await mark_bitrix_item_deleted(session, rec, "событие удаления")
        await session.commit()


async def sync_reclamation_from_bitrix(item_id: str) -> None:
    """Применяет реальное состояние элемента Bitrix к нашей рекламации —
    вызывается из вебхука ONCRMDYNAMICITEMUPDATE (см.
    bitrix_webhook_service.handle_reclamation_webhook), который сам несёт
    только ID элемента, без полей, поэтому дотягиваем элемент целиком
    (crm.item.get). Своя сессия — вызывается не из метода сервиса, а
    напрямую из обработчика вебхука, никакой session с request нет."""
    from app.database import AsyncSessionLocal
    from app.repositories.reclamation import ReclamationRepository
    from app.services import bitrix_service

    async with AsyncSessionLocal() as session:
        rec = await ReclamationRepository(session).find_by_bitrix_item_id(item_id)
        if rec is None:
            _log.info("Bitrix reclamation webhook: элемент %s не привязан ни к одной рекламации", item_id)
            return

        try:
            item = await bitrix_service.get_reclamation_item(item_id)
        except Exception:
            _log.exception("Bitrix reclamation webhook: не удалось получить элемент %s", item_id)
            return
        if item is None:
            _log.info("Bitrix reclamation webhook: crm.item.get не вернул элемент %s", item_id)
            return

        # Дедлайн и ответственного тянем обратно всегда — их могли поменять
        # прямо в карточке, минуя нашу админку. *_changed нужны, чтобы правка
        # доехала до БД и на ранних выходах ниже, где статус мы менять не станем
        stage_id = item.get("stageId")
        bitrix_deadline = bitrix_service.parse_reclamation_deadline(item)
        deadline_changed = bitrix_deadline != rec.deadline_at
        if deadline_changed:
            _log.info(
                "Bitrix reclamation webhook: рекламация %s — дедлайн %s -> %s",
                rec.id, rec.deadline_at, bitrix_deadline,
            )
            rec.deadline_at = bitrix_deadline

        raw_assignee = item.get("assignedById")
        bitrix_assignee = int(raw_assignee) if raw_assignee else None
        assignee_changed = bitrix_assignee != rec.responsible_bitrix_user_id
        if assignee_changed:
            bitrix_user = await bitrix_service.get_bitrix_user(bitrix_assignee) if bitrix_assignee else None
            _log.info(
                "Bitrix reclamation webhook: рекламация %s — ответственный %s -> %s",
                rec.id, rec.responsible_bitrix_user_id, bitrix_assignee,
            )
            rec.responsible_bitrix_user_id = bitrix_assignee
            # Резолвится best-effort: если Bitrix недоступен или сотрудник не
            # найден, ID всё равно сохраняем, а текстовые поля просто не трогаем
            # — лучше устаревшее ФИО, чем стереть контакт, который заявитель уже видел
            if bitrix_user is not None:
                rec.responsible_name = bitrix_user["full_name"]
                rec.responsible_phone = bitrix_user["phone"]
            elif bitrix_assignee is None:
                rec.responsible_name = None
                rec.responsible_phone = None

        # Гарантию тоже тянем обратно, но осторожнее: parse_reclamation_warranty
        # намеренно бросает исключение вместо None при сбое резолва (сеть,
        # незнакомый ID варианта) — иначе временный сбой мог бы затереть уже
        # известную классификацию, а не просто оставить её как есть
        try:
            bitrix_warranty = await bitrix_service.parse_reclamation_warranty(item)
            warranty_changed = bitrix_warranty != rec.warranty_classification
            if warranty_changed:
                _log.info(
                    "Bitrix reclamation webhook: рекламация %s — гарантия %s -> %s",
                    rec.id, rec.warranty_classification, bitrix_warranty,
                )
                rec.warranty_classification = bitrix_warranty
        except Exception:
            _log.exception(
                "Bitrix reclamation webhook: не удалось прочитать гарантию у рекламации %s, "
                "оставляю как есть", rec.id,
            )
            warranty_changed = False

        # Пока у рекламации висит неотправленная смена статуса, карточка в
        # Bitrix заведомо отстала от нас, и принимать из неё статус нельзя:
        # иначе наш же неудавшийся push откатывает то, что админ только что
        # выставил. Случилось 2026-09-23 на №16 — обновление ответственного
        # вернулось вебхуком раньше, чем уехала стадия, и resolved сам стал
        # review.
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        if await ReclamationOutboxRepository(session).has_pending_status_change(rec.id):
            if deadline_changed or assignee_changed or warranty_changed:
                await session.commit()
            _log.info(
                "Bitrix reclamation webhook: рекламация %s — есть неотправленная смена статуса, "
                "статус из Bitrix (стадия %s) не применяю",
                rec.id, stage_id,
            )
            return

        new_status = bitrix_service.RECLAMATION_STAGE_TO_STATUS.get(stage_id)
        if new_status is None:
            if deadline_changed or assignee_changed or warranty_changed:
                await session.commit()
            _log.info(
                "Bitrix reclamation webhook: неизвестная стадия %s у элемента %s", stage_id, item_id,
            )
            return
        if new_status == rec.status:
            if deadline_changed or assignee_changed or warranty_changed:
                await session.commit()
            _log.info(
                "Bitrix reclamation webhook: рекламация %s уже в статусе %s, пропускаю",
                rec.id, new_status,
            )
            return

        old_status = rec.status
        rec.status = new_status
        if new_status in ("resolved", "rejected", "invalid"):
            if rec.resolved_at is None:
                rec.resolved_at = datetime.now(timezone.utc)
        else:
            # вернули в работу — даты закрытия у рекламации больше нет
            rec.resolved_at = None

        await session.commit()
        _log.info(
            "Bitrix reclamation webhook: рекламация %s статус %s -> %s (item=%s, stage=%s)",
            rec.id, old_status, new_status, item_id, stage_id,
        )

        await ReclamationService(session)._notify_status_change(rec)


async def retry_bitrix_outbox() -> None:
    """Раз в 15 минут (см. main.py) разбирает недоставленные попытки
    синхронизации с Bitrix (п.8 ТЗ, ReclamationBitrixOutbox) — повторяет их
    теми же данными, что были на момент сбоя (payload), не текущим
    состоянием рекламации (оно могло уйти дальше за это время). При успехе
    строка удаляется, при повторном сбое — attempts++/last_error обновляются,
    без ограничения на число попыток (видно администратору интеграции через
    GET /admin/reclamations/bitrix-outbox, разбираться вручную, если застряло
    надолго — см. ReclamationService.retry_outbox_now для ручной правки)."""
    from app.database import AsyncSessionLocal
    from app.repositories.reclamation_outbox import ReclamationOutboxRepository

    async with AsyncSessionLocal() as session:
        outbox_repo = ReclamationOutboxRepository(session)
        rows = await outbox_repo.list_pending()

        for row in rows:
            await _retry_outbox_row(session, outbox_repo, row)
            await session.commit()


async def _retry_outbox_row(session, outbox_repo, row) -> bool:
    """Одна попытка повтора — общая для фонового retry_bitrix_outbox (по всем
    строкам, раз в 15 минут) и ручного повтора админом сразу после правки
    payload через PATCH /admin/reclamations/bitrix-outbox/{id}, чтобы увидеть
    результат правки тут же, а не ждать следующего цикла. Коммит — на
    вызывающей стороне, один раз после вызова этой функции."""
    from app.core.signed_urls import sign_url
    from app.services import bitrix_service

    try:
        if row.operation == "create":
            rec = await session.get(Reclamation, row.reclamation_id)
            if rec is None:
                await outbox_repo.delete(row)
                return True
            if rec.bitrix_item_id:
                # уже создалось как-то иначе (например, починили руками) — не дублируем
                await outbox_repo.delete(row)
                return True
            item_id = await bitrix_service.create_reclamation_item(
                row.payload["description"], row.payload.get("deal_id"),
                row.payload.get("company_id"), sign_url(row.payload.get("attachment_url")),
                row.payload.get("project_name"), row.payload.get("object_serial_number"),
                row.payload.get("contract_info"), row.payload.get("component_info"),
            )
            if not item_id:
                raise RuntimeError("Bitrix не настроен (BITRIX_WEBHOOK_URL пуст)")
            rec.bitrix_item_id = item_id

        elif row.operation == "status":
            rec = await session.get(Reclamation, row.reclamation_id)
            bitrix_item_id = rec.bitrix_item_id if rec is not None else None
            if not bitrix_item_id:
                raise RuntimeError("У рекламации всё ещё нет bitrix_item_id (create не прошёл)")
            saved_deadline = row.payload.get("deadline")
            pushed_stage = await bitrix_service.update_reclamation_stage(
                bitrix_item_id, row.payload["status"], sign_url(row.payload.get("confirmation_file_url")),
                date.fromisoformat(saved_deadline) if saved_deadline else None,
            )
            if pushed_stage:
                # Приводим статус к тому, что реально уехало в Bitrix.
                # Пока попытка висела в очереди, наш статус мог
                # откатиться вебхуком — карточка-то стояла на старой
                # стадии. Ждать, что вебхук от этого же обновления всё
                # исправит сам, нельзя: он прилетает раньше, чем
                # закоммитится удаление строки ниже, и его отсечёт
                # защита в sync_reclamation_from_bitrix
                rec.status = row.payload["status"]

        elif row.operation == "deadline":
            rec = await session.get(Reclamation, row.reclamation_id)
            bitrix_item_id = rec.bitrix_item_id if rec is not None else None
            if not bitrix_item_id:
                raise RuntimeError("У рекламации всё ещё нет bitrix_item_id (create не прошёл)")
            saved = row.payload.get("deadline")
            await bitrix_service.update_reclamation_deadline(
                bitrix_item_id, date.fromisoformat(saved) if saved else None,
            )

        elif row.operation == "assignee":
            rec = await session.get(Reclamation, row.reclamation_id)
            bitrix_item_id = rec.bitrix_item_id if rec is not None else None
            if not bitrix_item_id:
                raise RuntimeError("У рекламации всё ещё нет bitrix_item_id (create не прошёл)")
            await bitrix_service.update_reclamation_assignee(
                bitrix_item_id, row.payload["bitrix_user_id"],
            )

        elif row.operation == "warranty":
            rec = await session.get(Reclamation, row.reclamation_id)
            bitrix_item_id = rec.bitrix_item_id if rec is not None else None
            if not bitrix_item_id:
                raise RuntimeError("У рекламации всё ещё нет bitrix_item_id (create не прошёл)")
            await bitrix_service.update_reclamation_warranty(
                bitrix_item_id, row.payload["warranty"],
            )

        elif row.operation == "comment":
            rec = await session.get(Reclamation, row.reclamation_id)
            bitrix_item_id = rec.bitrix_item_id if rec is not None else None
            if not bitrix_item_id:
                raise RuntimeError("У рекламации всё ещё нет bitrix_item_id (create не прошёл)")
            await bitrix_service.add_reclamation_comment(
                bitrix_item_id, row.payload["text"],
            )

        else:
            _log.warning("Reclamation outbox: неизвестная операция %s (id=%s)", row.operation, row.id)
            return False

        await outbox_repo.delete(row)
        _log.info("Reclamation outbox: повтор успешен (id=%s, operation=%s)", row.id, row.operation)
        return True
    except Exception as exc:
        # Удалённую карточку повторять бессмысленно — отвязываем
        # рекламацию, и это разом снимает все её операции из очереди
        rec = await session.get(Reclamation, row.reclamation_id)
        if rec is not None and rec.bitrix_item_id and _is_item_gone(str(exc)):
            await mark_bitrix_item_deleted(session, rec, "NOT_FOUND при повторе")
        else:
            outbox_repo.mark_failed_attempt(row, str(exc))
            _log.warning(
                "Reclamation outbox: повтор не удался (id=%s, operation=%s, попытка %s): %s",
                row.id, row.operation, row.attempts, exc,
            )
        return False


def _build_bitrix_description(rec: Reclamation) -> str:
    """Текстовая сводка в sourceDescription — только то, подо что в
    смарт-процессе НЕТ своего поля. Договор/заказ/ТТН, заводской номер и данные
    ПКИ раньше дублировались сюда текстом, теперь у них есть нативные поля
    (см. bitrix_service.create_reclamation_item), и в описании им делать нечего."""
    lines = [rec.description, "", "--- Дополнительно (Savt Assist) ---"]
    # для cabinet/line/component object_details целиком уходит в свои поля
    # (см. _build_bitrix_native_fields), а для software/documentation
    # своего поля в Bitrix нет — только тут его и покажем, читаемым текстом,
    # а не питоновским repr словаря
    if rec.object_details and rec.object_type not in ("cabinet", "line", "component"):
        details_text = ", ".join(f"{k}: {v}" for k, v in rec.object_details.items() if v)
        if details_text:
            lines.append(f"Данные объекта: {details_text}")
    lines.append(f"Контакт: {rec.contact_name}, {rec.contact_phone}, {rec.contact_email}")
    if rec.customer_name:
        lines.append(f"Заказчик: {rec.customer_name}")
    if rec.occurrence_conditions:
        lines.append(f"Условия проявления: {rec.occurrence_conditions}")
    if rec.error_codes:
        lines.append(f"Коды ошибок: {rec.error_codes}")
    lines.append("")
    lines.append(f"Подробнее: {settings.reclamation_admin_url}?tab=reclamations&reclamation_id={rec.id}")
    return "\n".join(lines)