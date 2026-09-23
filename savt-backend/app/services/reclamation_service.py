import asyncio
import logging

from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.models.reclamation import Reclamation
from app.repositories.cabinet import CabinetRepository
from app.repositories.reclamation import ReclamationRepository
from app.schemas.pagination import PageOut, make_page
from app.schemas.reclamation import (
    AdminReclamationListItemOut,
    AdminReclamationOut,
    BitrixUserOut,
    ReclamationAttachmentOut,
    ReclamationCreateIn,
    ReclamationDetailOut,
    ReclamationListItemOut,
    ReclamationOutboxOut,
)
from app.services.audit_service import AuditLogger
from app.services.notification_service import NotificationService

_log = logging.getLogger(__name__)


class ReclamationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ReclamationRepository(session)
        self.cabinet_repo = CabinetRepository(session)
        self.audit = AuditLogger(session)

    # --- пользователь ---

    async def create(self, user_id: int, data: ReclamationCreateIn) -> ReclamationDetailOut:
        if data.object_type == "cabinet":
            if data.cabinet_id is None:
                raise ValidationError("Для объекта «ШУ» нужно выбрать конкретный шкаф")
            if not await self.cabinet_repo.user_has_access(user_id, data.cabinet_id):
                raise PermissionDeniedError("У вас нет доступа к этому ШУ")

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
            rec.id, _build_bitrix_description(rec), rec.cabinet_id, first_attachment_url,
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
            ReclamationListItemOut(**self._list_fields(rec, cabinet))
            for rec, cabinet in rows
        ]
        return make_page(items, total, page, size)

    # --- админка: пока без Битрикса роль "закреплённых специалистов" временно
    # исполняет админ вручную, см. Reclamation.__doc__ ---

    async def list_admin(
        self,
        status: str | None, object_type: str | None, warranty_classification: bool | None,
        page: int, size: int,
    ) -> PageOut[AdminReclamationListItemOut]:
        rows, total = await self.repo.list_admin(
            status, object_type, warranty_classification, (page - 1) * size, size,
        )
        items = [
            AdminReclamationListItemOut(
                **self._list_fields(rec, cabinet), user_id=user.id, user_full_name=user.full_name,
                deadline_at=rec.deadline_at,
            )
            for rec, user, cabinet in rows
        ]
        return make_page(items, total, page, size)

    async def get_admin(self, reclamation_id: int) -> AdminReclamationOut:
        row = await self.repo.get_with_relations(reclamation_id)
        if row is None:
            raise NotFoundError("Рекламация не найдена")
        rec, user, cabinet = row
        detail = await self._detail_out(rec, cabinet)
        return AdminReclamationOut(
            **detail.model_dump(), user_id=user.id, user_full_name=user.full_name,
            deadline_at=rec.deadline_at, bitrix_item_id=rec.bitrix_item_id,
        )

    async def update(
        self, reclamation_id: int, changed: dict, actor_id: int, actor_role: str,
    ) -> AdminReclamationOut:
        rec = await self.repo.get_by_id(reclamation_id)
        if rec is None:
            raise NotFoundError("Рекламация не найдена")

        # не колонка модели — используется только для проброса assignedById в
        # Bitrix ниже, в БД у нас ничего не хранит (см. AdminReclamationUpdateIn)
        responsible_bitrix_user_id = changed.pop("responsible_bitrix_user_id", None)

        deadline_changed = "deadline_at" in changed and changed["deadline_at"] != rec.deadline_at

        status_changed = "status" in changed and changed["status"] != rec.status
        if status_changed:
            self._check_transition(rec, changed)

        for field, value in changed.items():
            setattr(rec, field, value)
        if status_changed and rec.status in ("resolved", "rejected", "invalid"):
            rec.resolved_at = datetime.now(timezone.utc)

        # попнутые выше поля в changed уже не попадут, а это действия админа
        # на самом портале — в аудите они нужны не меньше остальных
        audit_meta = {"fields": list(changed.keys())}
        if responsible_bitrix_user_id is not None:
            audit_meta["responsible_bitrix_user_id"] = responsible_bitrix_user_id
        self.audit.log(
            "reclamation.update", "reclamation", rec.id, actor_id, actor_role, audit_meta,
        )
        await self.session.commit()

        # Ответственного отправляем в той же задаче ПОСЛЕ стадии, а не
        # параллельно с ней: любое наше обновление карточки возвращается к нам
        # вебхуком, и если ответственный уедет раньше стадии, вебхук прочитает
        # ещё старую стадию и откатит статус (ровно так 2026-09-23 у №16
        # выставленный админом resolved сам вернулся в review)
        assignee_to_push = responsible_bitrix_user_id if rec.bitrix_item_id else None

        if status_changed:
            await self._notify_status_change(rec)
            if rec.bitrix_item_id:
                # дедлайн уезжает вместе со стадией — Bitrix всё равно требует
                # его заполненным при переходе, отдельный вызов был бы лишним
                _sync_status_to_bitrix(
                    rec.id, rec.bitrix_item_id, rec.status, rec.confirmation_file_url,
                    rec.deadline_at, assignee_to_push,
                )
                assignee_to_push = None
                deadline_changed = False

        if deadline_changed and rec.bitrix_item_id:
            _sync_deadline_to_bitrix(rec.id, rec.bitrix_item_id, rec.deadline_at)

        if assignee_to_push:
            _sync_assignee_to_bitrix(rec.id, rec.bitrix_item_id, assignee_to_push)

        return await self.get_admin(reclamation_id)

    @staticmethod
    async def list_bitrix_users() -> list[BitrixUserOut]:
        from app.services import bitrix_service
        users = await bitrix_service.list_reclamation_assignees()
        return [BitrixUserOut(**u) for u in users]

    # Для "администратора интеграции" из ТЗ (п.3 — "обрабатывает ошибки
    # интеграции") — что сейчас не долетело до Bitrix и почему
    async def list_outbox(self) -> list[ReclamationOutboxOut]:
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        rows = await ReclamationOutboxRepository(self.session).list_pending()
        return [ReclamationOutboxOut.model_validate(r) for r in rows]

    # Обязательные проверки из п.8 ТЗ — завязаны на итоговое состояние заявки
    # (текущее значение + то, что меняется этим PATCH), поэтому в сервисе, не
    # в схеме, см. AdminReclamationUpdateIn.__doc__
    @staticmethod
    def _check_transition(rec, changed: dict) -> None:
        new_status = changed["status"]
        rejection_reason = changed.get("rejection_reason", rec.rejection_reason)
        resolution_comment = changed.get("resolution_comment", rec.resolution_comment)
        responsible_name = changed.get("responsible_name", rec.responsible_name)
        warranty_classification = changed.get("warranty_classification", rec.warranty_classification)
        confirmation_file_url = changed.get("confirmation_file_url", rec.confirmation_file_url)

        if new_status in ("rejected", "invalid") and not rejection_reason:
            raise ValidationError("Нельзя отклонить рекламацию без указания причины")
        if new_status == "resolved" and not resolution_comment:
            raise ValidationError("Нельзя закрыть рекламацию без итогового комментария")
        if new_status == "resolved" and not confirmation_file_url:
            raise ValidationError("Нельзя закрыть рекламацию без подтверждающего документа")
        if new_status == "in_progress":
            if not responsible_name:
                raise ValidationError("Нельзя перевести рекламацию в работу без ответственного лица")
            if warranty_classification is None:
                raise ValidationError(
                    "Нельзя перевести рекламацию в работу без классификации (гарантия/не гарантия)"
                )

    async def _notify_status_change(self, rec) -> None:
        if rec.status == "in_progress":
            label = "В работе. Гарантия" if rec.warranty_classification else "В работе. Не гарантия"
            body = f"Статус изменён: «{label}»"
            if rec.responsible_name:
                body += f". Ответственный: {rec.responsible_name}"
                if rec.responsible_phone:
                    body += f", тел. {rec.responsible_phone}"
        elif rec.status == "review":
            body = "Рекламация принята на рассмотрение"
        elif rec.status == "rejected":
            body = f"Рекламация отклонена. Причина: {rec.rejection_reason}"
        elif rec.status == "invalid":
            body = f"Рекламация оформлена некорректно. Причина: {rec.rejection_reason}"
        elif rec.status == "resolved":
            body = f"Рекламация исполнена. {rec.resolution_comment}"
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
    def _list_fields(rec, cabinet) -> dict:
        return dict(
            id=rec.id, object_type=rec.object_type, status=rec.status,
            warranty_classification=rec.warranty_classification,
            description=rec.description,
            cabinet_object_number=cabinet.object_number if cabinet else None,
            created_at=rec.created_at, resolved_at=rec.resolved_at,
        )

    async def _detail_out(self, rec, cabinet) -> ReclamationDetailOut:
        attachments = await self.repo.list_attachments(rec.id)
        return ReclamationDetailOut(
            id=rec.id, status=rec.status, warranty_classification=rec.warranty_classification,
            object_type=rec.object_type, cabinet_id=rec.cabinet_id,
            cabinet_object_number=cabinet.object_number if cabinet else None,
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
# asyncio.create_task в своей собственной сессии, к моменту её реального
# выполнения request-сессия (self.session) может быть уже закрыта — как и у
# ServiceRequestService._sync_to_bitrix, см. app/services/service_request_service.py

def _build_bitrix_native_fields(
    object_type: str, object_details: dict | None, cabinet,
    contract_number: str | None, order_number: str | None, ttn_number: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Собирает значения для трёх новых нативных полей процесса (появились
    2026-09-23) — заводской номер, № договора/заказа/ТТН, данные ПКИ.
    Возвращает (object_serial_number, contract_info, component_info)."""
    object_serial_number = component_info = None
    if object_type == "cabinet" and cabinet is not None:
        object_serial_number = cabinet.object_number
    elif object_type == "line" and object_details:
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
    reclamation_id: int, description: str, cabinet_id: int | None, attachment_url: str | None,
    object_type: str, object_details: dict | None,
    contract_number: str | None, order_number: str | None, ttn_number: str | None,
) -> None:
    async def _task():
        from app.database import AsyncSessionLocal
        from app.models.cabinets import Cabinet
        from app.models.project import Project
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        from app.services import bitrix_service

        deal_id = company_id = project_name = None
        cabinet = None
        async with AsyncSessionLocal() as session:
            if cabinet_id is not None:
                cabinet = await session.get(Cabinet, cabinet_id)
                if cabinet is not None and cabinet.project_id is not None:
                    project = await session.get(Project, cabinet.project_id)
                    if project is not None:
                        deal_id = project.bitrix_deal_id
                        company_id = project.bitrix_company_id
                        project_name = project.name

            object_serial_number, contract_info, component_info = _build_bitrix_native_fields(
                object_type, object_details, cabinet, contract_number, order_number, ttn_number,
            )

            try:
                item_id = await bitrix_service.create_reclamation_item(
                    description, deal_id, company_id, attachment_url, project_name,
                    object_serial_number, contract_info, component_info,
                )
                if not item_id:
                    return  # Bitrix не настроен вообще — не сбой, повторять нечего
            except Exception as exc:
                _log.exception("Bitrix item creation failed for reclamation %s", reclamation_id)
                await ReclamationOutboxRepository(session).create(
                    reclamation_id, "create",
                    {
                        "description": description, "deal_id": deal_id, "company_id": company_id,
                        "attachment_url": attachment_url, "project_name": project_name,
                        "object_serial_number": object_serial_number, "contract_info": contract_info,
                        "component_info": component_info,
                    },
                    str(exc),
                )
                await session.commit()
                return

            rec = await session.get(Reclamation, reclamation_id)
            if rec is not None:
                rec.bitrix_item_id = item_id
                await session.commit()

    asyncio.create_task(_task())

def _sync_status_to_bitrix(
    reclamation_id: int, bitrix_item_id: str, status: str, confirmation_file_url: str | None,
    deadline: date | None = None, assignee_id: int | None = None,
) -> None:
    """assignee_id отправляется здесь же, строго после стадии — почему именно
    так, а не параллельно, см. комментарий в ReclamationService.update."""
    async def _task():
        from app.database import AsyncSessionLocal
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        from app.services import bitrix_service
        try:
            await bitrix_service.update_reclamation_stage(
                bitrix_item_id, status, confirmation_file_url, deadline,
            )
        except Exception as exc:
            _log.exception("Bitrix status sync failed for reclamation item %s", bitrix_item_id)
            async with AsyncSessionLocal() as session:
                await ReclamationOutboxRepository(session).create(
                    reclamation_id, "status",
                    {
                        "status": status, "confirmation_file_url": confirmation_file_url,
                        "deadline": deadline.isoformat() if deadline else None,
                    },
                    str(exc),
                )
                await session.commit()

        # Ответственного шлём в любом случае — админ его назначил, и от того,
        # уехала стадия или нет, это не зависит
        if assignee_id:
            await _push_assignee(reclamation_id, bitrix_item_id, assignee_id)

    asyncio.create_task(_task())


async def _push_assignee(reclamation_id: int, bitrix_item_id: str, bitrix_user_id: int) -> None:
    from app.database import AsyncSessionLocal
    from app.repositories.reclamation_outbox import ReclamationOutboxRepository
    from app.services import bitrix_service
    try:
        await bitrix_service.update_reclamation_assignee(bitrix_item_id, bitrix_user_id)
    except Exception as exc:
        _log.exception(
            "Bitrix assignee sync failed for reclamation item %s (user %s)",
            bitrix_item_id, bitrix_user_id,
        )
        async with AsyncSessionLocal() as session:
            await ReclamationOutboxRepository(session).create(
                reclamation_id, "assignee", {"bitrix_user_id": bitrix_user_id}, str(exc),
            )
            await session.commit()


def _sync_deadline_to_bitrix(reclamation_id: int, bitrix_item_id: str, deadline: date | None) -> None:
    """Срок поменяли без смены статуса. Когда статус меняется тем же запросом,
    дедлайн уезжает не отсюда, а вместе со стадией (Bitrix всё равно требует
    его при переходе) — см. ReclamationService.update."""
    async def _task():
        from app.database import AsyncSessionLocal
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        from app.services import bitrix_service
        try:
            await bitrix_service.update_reclamation_deadline(bitrix_item_id, deadline)
        except Exception as exc:
            _log.exception("Bitrix deadline sync failed for reclamation item %s", bitrix_item_id)
            async with AsyncSessionLocal() as session:
                await ReclamationOutboxRepository(session).create(
                    reclamation_id, "deadline",
                    {"deadline": deadline.isoformat() if deadline else None},
                    str(exc),
                )
                await session.commit()

    asyncio.create_task(_task())


def _sync_assignee_to_bitrix(reclamation_id: int, bitrix_item_id: str, bitrix_user_id: int) -> None:
    """Назначение ответственного само по себе, без смены статуса. Когда статус
    меняется тем же запросом, ответственный уезжает не отсюда, а из
    _sync_status_to_bitrix — строго после стадии."""
    asyncio.create_task(_push_assignee(reclamation_id, bitrix_item_id, bitrix_user_id))


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

        # Дедлайн тянем обратно всегда — его могли поменять прямо в карточке.
        # deadline_changed нужен, чтобы правка доехала до БД и на ранних
        # выходах ниже, где статус мы менять не станем
        stage_id = item.get("stageId")
        bitrix_deadline = bitrix_service.parse_reclamation_deadline(item)
        deadline_changed = bitrix_deadline != rec.deadline_at
        if deadline_changed:
            _log.info(
                "Bitrix reclamation webhook: рекламация %s — дедлайн %s -> %s",
                rec.id, rec.deadline_at, bitrix_deadline,
            )
            rec.deadline_at = bitrix_deadline

        # Пока у рекламации висит неотправленная смена статуса, карточка в
        # Bitrix заведомо отстала от нас, и принимать из неё статус нельзя:
        # иначе наш же неудавшийся push откатывает то, что админ только что
        # выставил. Случилось 2026-09-23 на №16 — обновление ответственного
        # вернулось вебхуком раньше, чем уехала стадия, и resolved сам стал
        # review.
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        if await ReclamationOutboxRepository(session).has_pending_status_change(rec.id):
            if deadline_changed:
                await session.commit()
            _log.info(
                "Bitrix reclamation webhook: рекламация %s — есть неотправленная смена статуса, "
                "статус из Bitrix (стадия %s) не применяю",
                rec.id, stage_id,
            )
            return

        new_status = bitrix_service.RECLAMATION_STAGE_TO_STATUS.get(stage_id)
        if new_status is None:
            if deadline_changed:
                await session.commit()
            _log.info(
                "Bitrix reclamation webhook: неизвестная стадия %s у элемента %s", stage_id, item_id,
            )
            return
        if new_status == rec.status:
            if deadline_changed:
                await session.commit()
            _log.info(
                "Bitrix reclamation webhook: рекламация %s уже в статусе %s, пропускаю",
                rec.id, new_status,
            )
            return

        old_status = rec.status
        rec.status = new_status
        if new_status in ("resolved", "rejected", "invalid") and rec.resolved_at is None:
            rec.resolved_at = datetime.now(timezone.utc)

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
    надолго)."""
    from app.database import AsyncSessionLocal
    from app.repositories.reclamation_outbox import ReclamationOutboxRepository
    from app.services import bitrix_service

    async with AsyncSessionLocal() as session:
        outbox_repo = ReclamationOutboxRepository(session)
        rows = await outbox_repo.list_pending()

        for row in rows:
            try:
                if row.operation == "create":
                    rec = await session.get(Reclamation, row.reclamation_id)
                    if rec is None:
                        await outbox_repo.delete(row)
                        await session.commit()
                        continue
                    if rec.bitrix_item_id:
                        # уже создалось как-то иначе (например, починили руками) — не дублируем
                        await outbox_repo.delete(row)
                        await session.commit()
                        continue
                    item_id = await bitrix_service.create_reclamation_item(
                        row.payload["description"], row.payload.get("deal_id"),
                        row.payload.get("company_id"), row.payload.get("attachment_url"),
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
                        bitrix_item_id, row.payload["status"], row.payload.get("confirmation_file_url"),
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

                else:
                    _log.warning("Reclamation outbox: неизвестная операция %s (id=%s)", row.operation, row.id)
                    continue

                await outbox_repo.delete(row)
                _log.info("Reclamation outbox: повтор успешен (id=%s, operation=%s)", row.id, row.operation)
            except Exception as exc:
                outbox_repo.mark_failed_attempt(row, str(exc))
                _log.warning(
                    "Reclamation outbox: повтор не удался (id=%s, operation=%s, попытка %s): %s",
                    row.id, row.operation, row.attempts, exc,
                )

            await session.commit()


def _build_bitrix_description(rec: Reclamation) -> str:
    lines = [rec.description, "", "--- Дополнительно (Savt Assist) ---"]
    if rec.object_details:
        lines.append(f"Данные объекта: {rec.object_details}")
    lines.append(f"Контакт: {rec.contact_name}, {rec.contact_phone}, {rec.contact_email}")
    if rec.customer_name:
        lines.append(f"Заказчик: {rec.customer_name}")
    if rec.contract_number or rec.order_number or rec.ttn_number:
        lines.append(
            f"Договор: {rec.contract_number or '-'}, заказ: {rec.order_number or '-'}, "
            f"ТТН: {rec.ttn_number or '-'}"
        )
    if rec.occurrence_conditions:
        lines.append(f"Условия проявления: {rec.occurrence_conditions}")
    if rec.error_codes:
        lines.append(f"Коды ошибок: {rec.error_codes}")
    lines.append("")
    lines.append(f"Подробнее: {settings.reclamation_admin_url}?tab=reclamations&reclamation_id={rec.id}")
    return "\n".join(lines)