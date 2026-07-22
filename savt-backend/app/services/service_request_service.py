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

_REQUEST_TYPE_LABELS = {
    "repair": "ремонт",
    "diagnostics": "диагностика",
    "remote_adjustment": "наладка удалённо",
    "onsite_adjustment": "наладка с выездом",
    "other": "другое",
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
        self, req_id: int, data: ServiceRequestStatusIn, actor_id: int = 0, actor_role: str = "admin"
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

        if req.bitrix_task_id:
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

        from app.repositories.cabinet import CabinetRepository
        from app.repositories.user import UserRepository
        cabinet = await CabinetRepository(self.session).get_by_id(req.cabinet_id)
        user = await UserRepository(self.session).get_by_id(req.user_id)
        return _to_detail(req, user, cabinet, chat_id)
