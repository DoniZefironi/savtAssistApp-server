from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.project import ProjectRepository, ProjectRequestRepository, UserProjectRepository
from app.schemas.pagination import PageOut, make_page
from app.schemas.requests import ApproveShareIn, ProjectShareRequestOut, RejectRequestIn
from app.services.audit_service import AuditLogger
from app.services.project_reconciliation import reconcile_cabinet_access


class ProjectRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.request_repo = ProjectRequestRepository(session)
        self.project_repo = ProjectRepository(session)
        self.user_project_repo = UserProjectRepository(session)
        self.audit = AuditLogger(session)

    # Все заявки на вступление в проект
    async def list_shares(
        self, status: str | None = None, resolved_by_admin_id: int | None = None,
        search: str | None = None,
        sort_by: str = "created_at", sort_order: str = "desc",
        page: int = 1, size: int = 20,
    ) -> PageOut[ProjectShareRequestOut]:
        rows, total = await self.request_repo.list_shares(
            status=status, resolved_by_admin_id=resolved_by_admin_id, search=search,
            sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size,
        )
        items = [
            ProjectShareRequestOut(
                id=req.id,
                user_id=req.user_id,
                user_full_name=user.full_name,
                user_phone=user.phone,
                user_type=user.user_type,
                organization_name=user.organization_name,
                user_is_verified=user.is_verified,
                user_registered_at=user.created_at,
                project_id=req.project_id,
                project_name=project.name,
                user_comment=req.user_comment,
                status=req.status,
                admin_response=req.admin_response,
                resolved_by_admin_id=req.resolved_by_admin_id,
                created_at=req.created_at,
                resolved_at=req.resolved_at,
            )
            for req, user, project in rows
        ]
        return make_page(items, total, page, size)

    # Апрув заявки — сразу даёт доступ ко всем шкафам проекта, включая занятые
    # посторонними (одно одобрение админа закрывает всё, второй заявки на
    # конкретный шкаф не требуется — см. project_reconciliation.reconcile_cabinet_access)
    async def approve_share(
        self, request_id: int, data: ApproveShareIn, admin_id: int, actor_role: str
    ) -> None:
        req = await self.request_repo.get_share(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        project = await self.project_repo.get_by_id(req.project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")

        existing = await self.user_project_repo.find(req.user_id, req.project_id)
        if existing is not None:
            raise AlreadyExistsError("Пользователь уже привязан к этому проекту")

        await self.user_project_repo.create(user_id=req.user_id, project_id=req.project_id, is_primary=False)

        created_chats = await reconcile_cabinet_access(
            self.session, req.project_id, [req.user_id], bypass_request=True,
        )

        req.status = "approved"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        self.audit.log("project_request.approve_share", "project_share_request", request_id,
                       admin_id, actor_role, {"user_id": req.user_id, "project_id": req.project_id})
        await self.session.commit()

        if created_chats:
            from app.services.chat_service import chat_summary_dict
            from app.services.realtime_events import publish_chat_created
            for chat in created_chats:
                await publish_chat_created(chat.id, chat_summary_dict(chat))

    # Не апрув заявки
    async def reject_share(
        self, request_id: int, data: RejectRequestIn, admin_id: int, actor_role: str
    ) -> None:
        req = await self.request_repo.get_share(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        req.status = "rejected"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        self.audit.log("project_request.reject_share", "project_share_request", request_id,
                       admin_id, actor_role, {"user_id": req.user_id, "reason": data.admin_response})
        await self.session.commit()
