import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.repositories.service_request import ServiceRequestRepository
from app.repositories.cabinet import UserCabinetRepository
from app.schemas.pagination import PageOut, make_page
from app.schemas.service_requests import (
    ServiceRequestCreateIn,
    ServiceRequestDetailOut,
    ServiceRequestOut,
    ServiceRequestStatusIn,
)
from app.services.audit_service import AuditLogger

_log = logging.getLogger(__name__)

_STATUS_LABELS = {
    "open": "новая",
    "in_progress": "в работе",
    "postponed": "отложена",
    "closed": "закрыта",
}

_REQUEST_TYPE_LABELS = {
    "repair": "ремонт",
    "diagnostics": "диагностика",
    "remote_adjustment": "наладка удалённо",
    "onsite_adjustment": "наладка с выездом",
    "other": "другое",
}

# Обратный маппинг статусов Bitrix (см. STATUS в tasks.task.* — 1 Новая, 2 Ждёт
# выполнения, 3 Выполняется, 4 Ждёт контроля, 5 Завершена, 6 Отложена, 7 Отклонена)
# в наши 4 статуса. "Отклонена" считаем закрытой (дальше по заявке работать не нужно)
_BITRIX_TO_STATUS = {
    "1": "open", "2": "open",
    "3": "in_progress", "4": "in_progress",
    "6": "postponed",
    "5": "closed", "7": "closed",
}


async def _get_chat_ids(session: AsyncSession, request_ids: list[int]) -> dict[int, int]:
    if not request_ids:
        return {}
    from app.models.chat import Chat
    result = await session.execute(
        select(Chat.service_request_id, Chat.id).where(Chat.service_request_id.in_(request_ids))
    )
    return {rid: cid for rid, cid in result.all()}


def _to_out(req, cabinet, chat_id: int | None = None) -> ServiceRequestOut:
    return ServiceRequestOut(
        id=req.id,
        user_id=req.user_id,
        cabinet_id=req.cabinet_id,
        cabinet_object_number=cabinet.object_number,
        request_type=req.request_type,
        description=req.description,
        status=req.status,
        bitrix_task_id=req.bitrix_task_id,
        chat_id=chat_id,
        created_at=req.created_at,
        closed_at=req.closed_at,
    )


def _to_detail(req, user, cabinet, chat_id: int | None = None) -> ServiceRequestDetailOut:
    return ServiceRequestDetailOut(
        id=req.id,
        user_id=req.user_id,
        cabinet_id=req.cabinet_id,
        cabinet_object_number=cabinet.object_number,
        request_type=req.request_type,
        description=req.description,
        status=req.status,
        bitrix_task_id=req.bitrix_task_id,
        chat_id=chat_id,
        created_at=req.created_at,
        closed_at=req.closed_at,
        user_full_name=user.full_name,
        user_phone=user.phone,
        user_type=user.user_type,
        organization_name=user.organization_name,
        user_is_verified=user.is_verified,
        user_registered_at=user.created_at,
    )


def _build_task_title(
    object_number: str, org_or_name: str, purpose: str | None,
    admin_internal_name: str | None, type_label: str,
) -> str:
    """Пример: "26_001 Могилевский водоканал ПНС Вейно (П-228) ремонт" —
    номер объекта, организация/ФИО заявителя, назначение ШУ, рабочий код ШУ
    в скобках (если задан) и тип заявки. Отсутствующие части просто пропускаются."""
    parts = [object_number, org_or_name]
    if purpose:
        parts.append(purpose)
    if admin_internal_name:
        parts.append(f"({admin_internal_name})")
    parts.append(type_label)
    return " ".join(p for p in parts if p)


def _sync_to_bitrix(
    request_id: int, request_type: str, description: str,
    cabinet_object_number: str, cabinet_type: str, requester: str,
    org_or_name: str, cabinet_purpose: str | None, cabinet_admin_internal_name: str | None,
) -> None:
    async def _task():
        from app.database import AsyncSessionLocal
        from app.models.service_request import ServiceRequest
        from app.services import bitrix_service

        type_label = _REQUEST_TYPE_LABELS.get(request_type, request_type)
        title = _build_task_title(
            cabinet_object_number, org_or_name, cabinet_purpose, cabinet_admin_internal_name, type_label,
        )
        body = (
            f"Заявка №{request_id} из приложения SAVT\n"
            f"ШУ: {cabinet_object_number} ({cabinet_type})\n"
            f"Тип: {type_label}\n"
            f"От: {requester}\n\n"
            f"{description}"
        )
        try:
            task_id = await bitrix_service.create_task(title, body)
        except Exception:
            _log.exception("Bitrix task creation failed for service request %s", request_id)
            return
        if not task_id:
            return
        try:
            async with AsyncSessionLocal() as session:
                req = await session.get(ServiceRequest, request_id)
                if req is not None:
                    req.bitrix_task_id = task_id
                    await session.commit()
        except Exception:
            _log.exception("Failed to save bitrix_task_id for service request %s", request_id)

    asyncio.create_task(_task())


def _sync_status_to_bitrix(bitrix_task_id: str, status: str) -> None:
    async def _task():
        from app.services import bitrix_service
        try:
            await bitrix_service.update_task_status(bitrix_task_id, status)
        except Exception:
            _log.exception("Bitrix status sync failed for task %s", bitrix_task_id)

    asyncio.create_task(_task())


# Синхронизация сообщения заявителя из чата заявки в комментарий Bitrix-задачи.
# Вызывается из ChatService.send_message — публичная (без "_"), т.к. используется
# из другого модуля. Только сообщения самого заявителя, не операторов/бота.
def sync_message_to_bitrix(
    service_request_id: int, sender_name: str, text: str | None, attachment_urls: list[str],
) -> None:
    async def _task():
        from app.database import AsyncSessionLocal
        from app.models.service_request import ServiceRequest
        from app.services import bitrix_service

        async with AsyncSessionLocal() as session:
            req = await session.get(ServiceRequest, service_request_id)
            task_id = req.bitrix_task_id if req is not None else None
        if not task_id:
            return

        parts = [text] if text else []
        parts.extend(attachment_urls)
        body = "\n".join(parts)
        comment = f'{sender_name} написал: "{body}"'
        try:
            await bitrix_service.add_comment(task_id, comment)
        except Exception:
            _log.exception("Bitrix comment sync failed for service request %s", service_request_id)

    asyncio.create_task(_task())


async def _apply_bitrix_status(service: "ServiceRequestService", req, bitrix_status: str | None) -> str | None:
    """Общая часть для планового опроса и вебхука ONTASKUPDATE: маппит статус
    Bitrix в наш и применяет, если он реально отличается. Возвращает новый статус,
    если применили (после вызова req.status уже перезаписан на него же), иначе None."""
    new_status = _BITRIX_TO_STATUS.get(bitrix_status) if bitrix_status else None
    if new_status is None or new_status == req.status:
        return None
    await service.update_status(
        req.id, ServiceRequestStatusIn(status=new_status),
        # actor_id=None, не 0 — audit_logs.actor_id это nullable FK на users.id,
        # а пользователя с id=0 не существует (упадёт с нарушением FK на commit)
        actor_id=None, actor_role="bitrix", sync_to_bitrix=False,
    )
    return new_status


# Опрос Bitrix за статусами задач (кто-то поменял статус прямо в Bitrix, минуя
# приложение) — вызывается из cron в main.py. Не трогает уже закрытые у нас заявки.
# Подстраховка на случай, если вебхук ONTASKUPDATE (sync_single_task_status) не
# настроен/не долетел — тогда статус всё равно подтянется в течение 15 минут.
async def sync_statuses_from_bitrix() -> None:
    from app.database import AsyncSessionLocal
    from app.models.service_request import ServiceRequest
    from app.services import bitrix_service

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ServiceRequest).where(
                ServiceRequest.bitrix_task_id.is_not(None),
                ServiceRequest.status != "closed",
            )
        )
        requests = list(result.scalars().all())
        if not requests:
            _log.info("Bitrix status poll: нет заявок с привязанным bitrix_task_id — опрос пропущен")
            return

        try:
            statuses = await bitrix_service.get_task_statuses(
                [r.bitrix_task_id for r in requests]
            )
        except Exception:
            _log.exception("Не удалось опросить статусы задач Bitrix")
            return

        _log.info(
            "Bitrix status poll: заявки=%s, получено статусов=%s",
            {r.id: r.bitrix_task_id for r in requests}, statuses,
        )

        service = ServiceRequestService(session)
        for req in requests:
            bitrix_status = statuses.get(req.bitrix_task_id)
            old_status = req.status
            try:
                new_status = await _apply_bitrix_status(service, req, bitrix_status)
                if new_status:
                    _log.info(
                        "Bitrix status poll: заявка %s task=%s статус %s -> %s (bitrix=%s)",
                        req.id, req.bitrix_task_id, old_status, new_status, bitrix_status,
                    )
            except Exception:
                _log.exception("Не удалось применить статус из Bitrix для заявки %s", req.id)


# Вебхук ONTASKUPDATE (см. bitrix_webhook_service.handle_task_update_webhook) —
# мгновенная реакция на смену статуса задачи вместо ожидания ближайшего опроса.
# Событие несёт только TASK_ID, не говорит, какое именно поле изменилось —
# поэтому просто перепроверяем текущий статус этой одной задачи.
async def sync_single_task_status(task_id: str) -> None:
    from app.database import AsyncSessionLocal
    from app.repositories.service_request import ServiceRequestRepository
    from app.services import bitrix_service

    async with AsyncSessionLocal() as session:
        req = await ServiceRequestRepository(session).find_by_bitrix_task_id(task_id)
        if req is None or req.status == "closed":
            return

        try:
            statuses = await bitrix_service.get_task_statuses([task_id])
        except Exception:
            _log.exception("Bitrix task webhook: не удалось получить статус задачи %s", task_id)
            return

        bitrix_status = statuses.get(task_id)
        old_status = req.status
        try:
            new_status = await _apply_bitrix_status(ServiceRequestService(session), req, bitrix_status)
            if new_status:
                _log.info(
                    "Bitrix task webhook: заявка %s task=%s статус %s -> %s (bitrix=%s)",
                    req.id, task_id, old_status, new_status, bitrix_status,
                )
        except Exception:
            _log.exception("Bitrix task webhook: не удалось применить статус для заявки %s", req.id)


class ServiceRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ServiceRequestRepository(session)
        self.user_cabinet_repo = UserCabinetRepository(session)
        self.audit = AuditLogger(session)

    async def create(self, user_id: int, data: ServiceRequestCreateIn) -> ServiceRequestOut:
        link = await self.user_cabinet_repo.find(user_id, data.cabinet_id)
        if link is None:
            raise PermissionDeniedError("У вас нет доступа к этому ШУ")

        req = await self.repo.create(
            user_id=user_id,
            cabinet_id=data.cabinet_id,
            request_type=data.request_type,
            description=data.description,
        )
        await self.session.flush()
        self.audit.log("service_request.create", "service_request", req.id, user_id, "user",
                       {"cabinet_id": data.cabinet_id, "type": data.request_type})

        from app.services.chat_service import ChatService, chat_summary_dict
        chat = await ChatService(self.session).ensure_service_request_chat(user_id, req.id, data.cabinet_id)

        await self.session.commit()
        await self.session.refresh(req)

        from app.services.realtime_events import publish_chat_created
        await publish_chat_created(chat.id, chat_summary_dict(chat))

        from app.repositories.cabinet import CabinetRepository
        from app.repositories.user import UserRepository
        cabinet = await CabinetRepository(self.session).get_by_id(data.cabinet_id)
        user = await UserRepository(self.session).get_by_id(user_id)
        requester = user.full_name or user.phone if user else str(user_id)
        org_or_name = (user.organization_name or user.full_name or user.phone) if user else str(user_id)
        _sync_to_bitrix(
            req.id, req.request_type, req.description, cabinet.object_number, cabinet.type, requester,
            org_or_name, cabinet.purpose, cabinet.admin_internal_name,
        )
        return _to_out(req, cabinet, chat.id)

    async def list_for_user(
        self, user_id: int, status: str | None, page: int, size: int
    ) -> PageOut[ServiceRequestOut]:
        rows, total = await self.repo.list_for_user(
            user_id, status, offset=(page - 1) * size, limit=size
        )
        chat_ids = await _get_chat_ids(self.session, [r.id for r, _ in rows])
        return make_page([_to_out(r, c, chat_ids.get(r.id)) for r, c in rows], total, page, size)

    async def list_admin(
        self, status: str | None, cabinet_id: int | None, page: int, size: int,
        request_type: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> PageOut[ServiceRequestDetailOut]:
        rows, total = await self.repo.list_admin(
            status, cabinet_id, request_type=request_type, search=search, sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size
        )
        chat_ids = await _get_chat_ids(self.session, [r.id for r, _, _ in rows])
        return make_page([_to_detail(r, u, c, chat_ids.get(r.id)) for r, u, c in rows], total, page, size)

    async def update_status(
        self, req_id: int, data: ServiceRequestStatusIn, actor_id: int | None = None, actor_role: str = "admin",
        sync_to_bitrix: bool = True,
    ) -> ServiceRequestDetailOut:
        req = await self.repo.get_by_id(req_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")

        old_status = req.status
        req.status = data.status
        if data.status == "closed":
            req.closed_at = datetime.now(timezone.utc)
        else:
            req.closed_at = None

        self.audit.log("service_request.status_change", "service_request", req_id, actor_id, actor_role,
                       {"old_status": old_status, "new_status": data.status})
        await self.session.commit()
        await self.session.refresh(req)

        # sync_to_bitrix=False — когда статус только что пришёл ИЗ Bitrix (см.
        # sync_statuses_from_bitrix), иначе он тут же уйдёт обратно по кругу
        if sync_to_bitrix and req.bitrix_task_id:
            _sync_status_to_bitrix(req.bitrix_task_id, req.status)

        # Закрытие заявки архивирует её чат (read-only, скрыт из активного списка);
        # повторное открытие — автоматически разархивирует
        from app.repositories.chat import ChatRepository
        chat = await ChatRepository(self.session).find_by_service_request(req_id)
        chat_id = chat.id if chat else None
        if chat is not None:
            should_be_archived = req.status == "closed"
            if should_be_archived != (chat.archived_at is not None):
                from app.services.chat_service import ChatService
                await ChatService(self.session).set_archived(chat.id, should_be_archived)
                # Закрытый чат больше не меняется — выгружаем стенограмму сразу,
                # не дожидаясь ночной сверки папок
                if should_be_archived:
                    from app.services.project_folder_service import schedule_request_chat_export
                    schedule_request_chat_export(chat.id)

        from app.repositories.cabinet import CabinetRepository
        from app.repositories.user import UserRepository
        cabinet = await CabinetRepository(self.session).get_by_id(req.cabinet_id)
        user = await UserRepository(self.session).get_by_id(req.user_id)

        # Заявитель узнаёт о смене статуса, а не выясняет её, заходя в приложение.
        # Только при реальном изменении — плановый опрос Bitrix идёт каждые 15 минут
        # и переписывал бы статус тем же значением.
        if old_status != req.status:
            from app.services.notification_service import NotificationService
            await NotificationService(self.session).send(
                user_id=req.user_id,
                type_="request_status",
                title="Заявка на обслуживание",
                body=f"Статус изменён: {_STATUS_LABELS.get(req.status, req.status)}",
                data={
                    "type": "service_request", "request_id": str(req_id),
                    "status": req.status, "chat_id": str(chat_id) if chat_id else "",
                },
            )

        return _to_detail(req, user, cabinet, chat_id)
