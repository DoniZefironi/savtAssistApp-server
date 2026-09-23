import asyncio
import logging

from datetime import datetime, timezone

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
        _sync_to_bitrix(rec.id, _build_bitrix_description(rec), rec.cabinet_id, first_attachment_url)

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
        return AdminReclamationOut(**detail.model_dump(), user_id=user.id, user_full_name=user.full_name)

    async def update(
        self, reclamation_id: int, changed: dict, actor_id: int, actor_role: str,
    ) -> AdminReclamationOut:
        rec = await self.repo.get_by_id(reclamation_id)
        if rec is None:
            raise NotFoundError("Рекламация не найдена")

        # не колонка модели — используется только для проброса assignedById в
        # Bitrix ниже, в БД у нас ничего не хранит (см. AdminReclamationUpdateIn)
        responsible_bitrix_user_id = changed.pop("responsible_bitrix_user_id", None)

        status_changed = "status" in changed and changed["status"] != rec.status
        if status_changed:
            self._check_transition(rec, changed)

        for field, value in changed.items():
            setattr(rec, field, value)
        if status_changed and rec.status in ("resolved", "rejected"):
            rec.resolved_at = datetime.now(timezone.utc)

        self.audit.log(
            "reclamation.update", "reclamation", rec.id, actor_id, actor_role,
            {"fields": list(changed.keys())},
        )
        await self.session.commit()

        if status_changed:
            await self._notify_status_change(rec)
            if rec.bitrix_item_id:
                _sync_status_to_bitrix(rec.id, rec.bitrix_item_id, rec.status, rec.confirmation_file_url)

        if responsible_bitrix_user_id and rec.bitrix_item_id:
            _sync_assignee_to_bitrix(rec.id, rec.bitrix_item_id, responsible_bitrix_user_id)

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

        if new_status == "rejected" and not rejection_reason:
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
        elif rec.status == "rejected":
            body = f"Рекламация отклонена. Причина: {rec.rejection_reason}"
        elif rec.status == "resolved":
            body = f"Рекламация исполнена. {rec.resolution_comment}"
        else:
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

def _sync_to_bitrix(
    reclamation_id: int, description: str, cabinet_id: int | None, attachment_url: str | None,
) -> None:
    async def _task():
        from app.database import AsyncSessionLocal
        from app.models.cabinets import Cabinet
        from app.models.project import Project
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        from app.services import bitrix_service

        deal_id = company_id = project_name = None
        async with AsyncSessionLocal() as session:
            if cabinet_id is not None:
                cabinet = await session.get(Cabinet, cabinet_id)
                if cabinet is not None and cabinet.project_id is not None:
                    project = await session.get(Project, cabinet.project_id)
                    if project is not None:
                        deal_id = project.bitrix_deal_id
                        company_id = project.bitrix_company_id
                        project_name = project.name

            try:
                item_id = await bitrix_service.create_reclamation_item(
                    description, deal_id, company_id, attachment_url, project_name,
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
) -> None:
    async def _task():
        from app.database import AsyncSessionLocal
        from app.repositories.reclamation_outbox import ReclamationOutboxRepository
        from app.services import bitrix_service
        try:
            await bitrix_service.update_reclamation_stage(bitrix_item_id, status, confirmation_file_url)
        except Exception as exc:
            _log.exception("Bitrix status sync failed for reclamation item %s", bitrix_item_id)
            async with AsyncSessionLocal() as session:
                await ReclamationOutboxRepository(session).create(
                    reclamation_id, "status",
                    {"status": status, "confirmation_file_url": confirmation_file_url},
                    str(exc),
                )
                await session.commit()

    asyncio.create_task(_task())


def _sync_assignee_to_bitrix(reclamation_id: int, bitrix_item_id: str, bitrix_user_id: int) -> None:
    async def _task():
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

    asyncio.create_task(_task())


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

        stage_id = item.get("stageId")
        new_status = bitrix_service.RECLAMATION_STAGE_TO_STATUS.get(stage_id)
        if new_status is None:
            _log.info(
                "Bitrix reclamation webhook: неизвестная стадия %s у элемента %s", stage_id, item_id,
            )
            return
        if new_status == rec.status:
            _log.info(
                "Bitrix reclamation webhook: рекламация %s уже в статусе %s, пропускаю",
                rec.id, new_status,
            )
            return

        old_status = rec.status
        rec.status = new_status
        if new_status in ("resolved", "rejected") and rec.resolved_at is None:
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
                        row.payload.get("project_name"),
                    )
                    if not item_id:
                        raise RuntimeError("Bitrix не настроен (BITRIX_WEBHOOK_URL пуст)")
                    rec.bitrix_item_id = item_id

                elif row.operation == "status":
                    rec = await session.get(Reclamation, row.reclamation_id)
                    bitrix_item_id = rec.bitrix_item_id if rec is not None else None
                    if not bitrix_item_id:
                        raise RuntimeError("У рекламации всё ещё нет bitrix_item_id (create не прошёл)")
                    await bitrix_service.update_reclamation_stage(
                        bitrix_item_id, row.payload["status"], row.payload.get("confirmation_file_url"),
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