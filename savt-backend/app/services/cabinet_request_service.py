from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.cabinet import CabinetRepository, CabinetRequestRepository
from app.repositories.project import ProjectRepository
from app.schemas.pagination import PageOut, make_page
from app.schemas.requests import (
    AdditionRequestOut,
    ApproveAdditionIn,
    RejectRequestIn,
)
from app.services.audit_service import AuditLogger


class CabinetRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.request_repo = CabinetRequestRepository(session)
        self.cabinet_repo = CabinetRepository(session)
        self.project_repo = ProjectRepository(session)
        self.audit = AuditLogger(session)

    # Заявитель узнаёт о решении, а не выясняет его, заходя в приложение.
    # Тип request_status — тот же переключатель в настройках уведомлений, что и
    # у остальных заявок.
    async def _notify(self, user_id: int, title: str, body: str | None, data: dict) -> None:
        from app.services.notification_service import NotificationService
        await NotificationService(self.session).send(
            user_id=user_id, type_="request_status",
            title=title, body=body or "Решение принято администратором", data=data,
        )

    # Все заявки на добавление по фото
    async def list_additions(
        self, status: str | None = None, resolved_by_admin_id: int | None = None,
        search: str | None = None,
        sort_by: str = "created_at", sort_order: str = "desc",
        page: int = 1, size: int = 20,
    ) -> PageOut[AdditionRequestOut]:
        rows, total = await self.request_repo.list_additions(
            status=status, resolved_by_admin_id=resolved_by_admin_id, search=search,
            sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size,
        )
        project_ids = list({req.project_id for req, _ in rows if req.project_id is not None})
        project_names = await self.project_repo.get_names_by_ids(project_ids)
        items = [
            AdditionRequestOut(
                id=req.id,
                user_id=req.user_id,
                user_full_name=user.full_name,
                user_phone=user.phone,
                user_type=user.user_type,
                organization_name=user.organization_name,
                user_is_verified=user.is_verified,
                user_registered_at=user.created_at,
                project_id=req.project_id,
                project_name=project_names.get(req.project_id) if req.project_id is not None else None,
                photo_url=req.photo_url,
                user_comment=req.user_comment,
                status=req.status,
                cabinet_id=req.cabinet_id,
                admin_response=req.admin_response,
                resolved_by_admin_id=req.resolved_by_admin_id,
                created_at=req.created_at,
                resolved_at=req.resolved_at,
            )
            for req, user in rows
        ]
        return make_page(items, total, page, size)

    # Апрув заявки — админ подтверждает, что фото соответствует конкретному ШУ.
    # Если у заявки указан project_id (заявитель уже состоит в этом проекте —
    # см. UserCabinetService.add_by_photo), тем же действием чинится и
    # принадлежность шкафа: ничейный ШУ привязывается к этому проекту, и все
    # его участники сразу получают доступ (шкаф уже в чужом проекте — 409,
    # переносить самовольно нельзя, сначала отвязать вручную).
    async def approve_addition(
        self, request_id: int, data: ApproveAdditionIn, admin_id: int, actor_role: str
    ) -> None:
        req = await self.request_repo.get_addition(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        cabinet = await self.cabinet_repo.get_by_id(data.cabinet_id)
        if cabinet is None or cabinet.deleted_at is not None:
            raise NotFoundError("ШУ не найден")

        if req.project_id is not None:
            if cabinet.project_id is not None and cabinet.project_id != req.project_id:
                raise AlreadyExistsError(
                    "Этот ШУ уже принадлежит другому проекту — сначала отвяжите его вручную"
                )
            if cabinet.project_id is None:
                cabinet.project_id = req.project_id
        # Чат ШУ никогда не создаётся автоматически — только сам пользователь,
        # открыв ШУ и нажав на чат (см. ChatService.get_cabinet_chat)

        req.status = "approved"
        req.cabinet_id = data.cabinet_id
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        self.audit.log("cabinet_request.approve_addition", "cabinet_addition_request", request_id,
                       admin_id, actor_role, {"user_id": req.user_id, "cabinet_id": data.cabinet_id})
        await self.session.commit()

        await self._notify(req.user_id, "Заявка на добавление ШУ одобрена",
                           f"ШУ {cabinet.object_number} добавлен в ваш список",
                           {"type": "cabinet_request", "cabinet_id": str(data.cabinet_id)})

    # Не апрув заявки
    async def reject_addition(
        self, request_id: int, data: RejectRequestIn, admin_id: int, actor_role: str
    ) -> None:
        req = await self.request_repo.get_addition(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        req.status = "rejected"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        self.audit.log("cabinet_request.reject_addition", "cabinet_addition_request", request_id,
                       admin_id, actor_role, {"user_id": req.user_id, "reason": data.admin_response})
        await self.session.commit()
        await self._notify(req.user_id, "Заявка на добавление ШУ отклонена",
                           data.admin_response, {"type": "cabinet_request"})
