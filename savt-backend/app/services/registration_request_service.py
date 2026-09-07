from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.registration_request import RegistrationRequestRepository
from app.repositories.user import UserRepository
from app.schemas.auth import (
    AdminRegistrationRequestOut,
    ApproveRegistrationRequestIn,
    RegistrationRequestCreateIn,
    RegistrationRequestOut,
)
from app.schemas.pagination import PageOut, make_page
from app.schemas.requests import RejectRequestIn
from app.services.audit_service import AuditLogger


class RegistrationRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.request_repo = RegistrationRequestRepository(session)
        self.user_repo = UserRepository(session)
        self.audit = AuditLogger(session)

    # Подать заявку — доступно без авторизации, заявителя ещё не существует
    # как пользователя. Пароль уже хешируется здесь и переносится как есть при
    # одобрении, чтобы у заявителя сразу работал именно тот пароль, что он ввёл.
    async def submit(self, data: RegistrationRequestCreateIn) -> RegistrationRequestOut:
        from app.core.security import hash_password

        if await self.user_repo.find_by_phone(data.phone) is not None:
            raise AlreadyExistsError("Пользователь с таким номером телефона уже зарегистрирован")
        if await self.request_repo.has_pending_for_phone(data.phone):
            raise AlreadyExistsError("Заявка с этим номером телефона уже рассматривается")

        req = await self.request_repo.create(
            phone=data.phone,
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
            user_type=data.user_type,
            organization_name=data.organization_name,
            contact_phone=data.contact_phone,
            user_comment=data.user_comment,
        )
        await self.session.commit()
        return RegistrationRequestOut(id=req.id, status=req.status, created_at=req.created_at)

    async def list_requests(
        self,
        status: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[AdminRegistrationRequestOut]:
        rows, total = await self.request_repo.list_requests(
            status=status, search=search, sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size,
        )
        items = [AdminRegistrationRequestOut.model_validate(r) for r in rows]
        return make_page(items, total, page, size)

    # Одобрение — заводит настоящий аккаунт из данных заявки. is_phone_verified/
    # is_verified=True сразу: решение админа здесь заменяет то, что в обычной
    # регистрации подтверждает Telegram (см. AdminUserService.create_user —
    # та же логика). Уведомить заявителя в приложении нечем — он ещё не
    # пользователь, узнаёт о решении вне приложения.
    async def approve(
        self, request_id: int, data: ApproveRegistrationRequestIn, admin_id: int, actor_role: str
    ) -> None:
        req = await self.request_repo.get(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")
        if await self.user_repo.find_by_phone(req.phone) is not None:
            raise AlreadyExistsError("Пользователь с таким номером телефона уже зарегистрирован")

        from sqlalchemy import select
        from app.models.role import Role

        role = (await self.session.execute(
            select(Role).where(Role.name == "user")
        )).scalar_one_or_none()
        if role is None:
            raise NotFoundError("Роль 'user' не найдена")

        user = await self.user_repo.create(
            phone=req.phone,
            contact_phone=req.contact_phone,
            full_name=req.full_name,
            user_type=req.user_type,
            organization_name=req.organization_name,
            hashed_password=req.hashed_password,
            role_id=role.id,
            is_active=True,
            is_phone_verified=True,
            is_verified=True,
        )
        await self.session.flush()

        from app.services.chat_service import ChatService, chat_summary_dict
        support_chat = await ChatService(self.session).ensure_support_and_notes(user.id)

        req.status = "approved"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)
        req.created_user_id = user.id

        self.audit.log("registration_request.approve", "registration_request", request_id,
                       admin_id, actor_role, {"phone": req.phone, "created_user_id": user.id})
        await self.session.commit()

        if support_chat is not None:
            from app.services.realtime_events import publish_chat_created
            await publish_chat_created(support_chat.id, chat_summary_dict(support_chat, user_name=user.full_name))

    async def reject(
        self, request_id: int, data: RejectRequestIn, admin_id: int, actor_role: str
    ) -> None:
        req = await self.request_repo.get(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        req.status = "rejected"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        self.audit.log("registration_request.reject", "registration_request", request_id,
                       admin_id, actor_role, {"phone": req.phone, "reason": data.admin_response})
        await self.session.commit()
