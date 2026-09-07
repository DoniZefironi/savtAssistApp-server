from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.password_reset_request import PasswordResetRequestRepository
from app.repositories.user import UserRepository
from app.schemas.auth import (
    AdminPasswordResetRequestOut,
    PasswordResetRequestCreateIn,
    PasswordResetRequestOut,
)
from app.schemas.pagination import PageOut, make_page
from app.schemas.requests import RejectRequestIn
from app.services.audit_service import AuditLogger


class PasswordResetRequestService:
    """Сброс пароля через заявку с одобрением администратора — для тех, у кого
    нет привязанного Telegram и кто поэтому не может пройти обычный
    AuthService.password_reset_* (там channel только "telegram"). Та же логика
    доверия, что у PhoneChangeService: систему не спрашивают, владеет ли
    заявитель аккаунтом — это вне приложения проверяет администратор."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PasswordResetRequestRepository(session)
        self.user_repo = UserRepository(session)
        self.audit = AuditLogger(session)

    # --- Заявитель (без авторизации — он не может войти, в этом и проблема) ---

    async def submit(self, data: PasswordResetRequestCreateIn) -> PasswordResetRequestOut:
        from app.core.security import hash_password

        user = await self.user_repo.find_by_phone(data.phone)
        if user is None or not user.is_active:
            raise NotFoundError("Пользователь с таким номером телефона не найден")
        if await self.repo.find_pending_for_user(user.id) is not None:
            raise AlreadyExistsError("У вас уже есть необработанная заявка на сброс пароля")

        req = await self.repo.create(
            user_id=user.id,
            hashed_password=hash_password(data.new_password),
            user_comment=data.user_comment,
        )
        self.audit.log(
            "password_reset_request.create", "password_reset_request", req.id, user.id, "user", {},
        )
        await self.session.commit()
        return PasswordResetRequestOut(id=req.id, status=req.status, created_at=req.created_at)

    # --- Администратор ---

    async def list_requests(
        self, status: str | None = None, search: str | None = None,
        sort_by: str = "created_at", sort_order: str = "desc",
        page: int = 1, size: int = 20,
    ) -> PageOut[AdminPasswordResetRequestOut]:
        rows, total = await self.repo.list_admin(
            status=status, search=search, sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size,
        )
        items = [
            AdminPasswordResetRequestOut(
                id=req.id,
                user_id=req.user_id,
                user_full_name=user.full_name,
                user_phone=user.phone,
                user_type=user.user_type,
                organization_name=user.organization_name,
                user_comment=req.user_comment,
                status=req.status,
                admin_response=req.admin_response,
                resolved_by_admin_id=req.resolved_by_admin_id,
                created_at=req.created_at,
                resolved_at=req.resolved_at,
            )
            for req, user in rows
        ]
        return make_page(items, total, page, size)

    # Одобрение применяет пароль, предложенный заявителем при подаче заявки,
    # и отзывает все текущие сессии — та же гигиена, что у самостоятельного
    # сброса (AuthService.password_reset_complete)
    async def approve(
        self, request_id: int, admin_response: str | None, admin_id: int, actor_role: str
    ) -> None:
        req = await self.repo.get_by_id(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        user = await self.user_repo.get_by_id(req.user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")

        user.hashed_password = req.hashed_password

        from app.repositories.auth import RefreshTokenRepository
        await RefreshTokenRepository(self.session).revoke_all_for_user(user.id)

        req.status = "approved"
        req.admin_response = admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        self.audit.log(
            "password_reset_request.approve", "password_reset_request", request_id,
            admin_id, actor_role, {"user_id": req.user_id},
        )
        await self.session.commit()

        from app.services.notification_service import NotificationService
        await NotificationService(self.session).send(
            user_id=req.user_id,
            type_="request_status",
            title="Пароль изменён",
            body="Ваш пароль сброшен администратором — используйте новый пароль для входа",
            data={"type": "password_reset", "request_id": str(request_id)},
        )

    async def reject(
        self, request_id: int, data: RejectRequestIn, admin_id: int, actor_role: str
    ) -> None:
        req = await self.repo.get_by_id(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        req.status = "rejected"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        self.audit.log(
            "password_reset_request.reject", "password_reset_request", request_id,
            admin_id, actor_role, {"user_id": req.user_id, "reason": data.admin_response},
        )
        await self.session.commit()

        from app.services.notification_service import NotificationService
        await NotificationService(self.session).send(
            user_id=req.user_id,
            type_="request_status",
            title="Заявка на сброс пароля отклонена",
            body=data.admin_response,
            data={"type": "password_reset", "request_id": str(request_id)},
        )
