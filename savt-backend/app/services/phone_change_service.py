from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.user import User
from app.repositories.phone_change import PhoneChangeRequestRepository
from app.repositories.user import UserRepository
from app.schemas.auth import AdminPhoneChangeRequestOut, PhoneChangeRequestOut
from app.schemas.pagination import PageOut, make_page
from app.schemas.requests import RejectRequestIn
from app.services.audit_service import AuditLogger


class PhoneChangeService:
    """Смена номера телефона через заявку с ручным одобрением администратора.

    Самообслуживание убрано намеренно: SMS отключены полностью, а код доставляется
    в мессенджер по user_id — то есть в собственный Telegram/Viber заявителя. Такой
    код не подтверждает владение новым номером ничем, и раньше любой пользователь
    мог поставить себе любой незанятый номер. Здесь владение проверяет живой
    администратор вне системы, а сервис лишь фиксирует решение и его автора."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PhoneChangeRequestRepository(session)
        self.user_repo = UserRepository(session)
        self.audit = AuditLogger(session)

    # --- Пользователь ---

    async def create_request(
        self, user: User, new_phone: str, user_comment: str | None
    ) -> PhoneChangeRequestOut:
        if user.phone == new_phone:
            raise AlreadyExistsError("Это уже ваш текущий номер")

        if await self.user_repo.find_by_phone(new_phone) is not None:
            raise AlreadyExistsError("Этот номер уже занят другим пользователем")

        if await self.repo.find_pending_for_user(user.id) is not None:
            raise AlreadyExistsError("У вас уже есть необработанная заявка на смену номера")

        req = await self.repo.create(
            user_id=user.id,
            new_phone=new_phone,
            old_phone=user.phone,
            user_comment=user_comment,
        )
        await self.session.flush()
        self.audit.log(
            "phone_change_request.create", "phone_change_request", req.id, user.id, "user",
            {"old_phone": user.phone, "new_phone": new_phone},
        )
        await self.session.commit()
        await self.session.refresh(req)
        return PhoneChangeRequestOut.model_validate(req)

    async def get_my_request(self, user_id: int) -> PhoneChangeRequestOut | None:
        req = await self.repo.find_pending_for_user(user_id)
        return PhoneChangeRequestOut.model_validate(req) if req else None

    async def cancel_my_request(self, user_id: int) -> None:
        req = await self.repo.find_pending_for_user(user_id)
        if req is None:
            raise NotFoundError("Активная заявка не найдена")
        req.status = "cancelled"
        req.resolved_at = datetime.now(timezone.utc)
        await self.session.commit()

    # --- Администратор ---

    async def list_requests(
        self, status: str | None = None, resolved_by_admin_id: int | None = None,
        search: str | None = None, sort_by: str = "created_at", sort_order: str = "desc",
        page: int = 1, size: int = 20,
    ) -> PageOut[AdminPhoneChangeRequestOut]:
        rows, total = await self.repo.list_admin(
            status=status, resolved_by_admin_id=resolved_by_admin_id, search=search,
            sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size,
        )

        # Сколько ещё аккаунтов претендуют на тот же номер — считаем разом
        # по всем номерам страницы, а не запросом на строку
        rivals: dict[str, int] = {}
        for req, _ in rows:
            if req.status == "pending" and req.new_phone not in rivals:
                rivals[req.new_phone] = len(await self.repo.find_pending_for_phone(req.new_phone))

        items = [
            AdminPhoneChangeRequestOut(
                id=req.id,
                user_id=req.user_id,
                new_phone=req.new_phone,
                old_phone=req.old_phone,
                user_comment=req.user_comment,
                status=req.status,
                admin_response=req.admin_response,
                resolved_by_admin_id=req.resolved_by_admin_id,
                created_at=req.created_at,
                resolved_at=req.resolved_at,
                user_full_name=user.full_name,
                user_type=user.user_type,
                organization_name=user.organization_name,
                user_is_verified=user.is_verified,
                user_registered_at=user.created_at,
                pending_rivals=rivals.get(req.new_phone, 1),
            )
            for req, user in rows
        ]
        return make_page(items, total, page, size)

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

        # Номер мог быть занят уже после подачи заявки — например, владелец сам
        # зарегистрировался, пока заявка ждала. Проверяем на момент одобрения,
        # а не только на момент подачи (та же логика, что в approve_share по ШУ)
        taken_by = await self.user_repo.find_by_phone(req.new_phone)
        if taken_by is not None and taken_by.id != user.id:
            raise AlreadyExistsError("Этот номер уже занят другим пользователем")

        old_phone = user.phone
        user.phone = req.new_phone

        req.status = "approved"
        req.admin_response = admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        self.audit.log(
            "phone_change_request.approve", "phone_change_request", request_id,
            admin_id, actor_role,
            {"user_id": req.user_id, "old_phone": old_phone, "new_phone": req.new_phone},
        )
        await self.session.commit()

        # Номер — это логин пользователя, о его смене надо сообщить.
        # После коммита: уведомление не должно откатывать саму смену.
        from app.services.notification_service import NotificationService
        await NotificationService(self.session).send(
            user_id=req.user_id,
            type_="request_status",
            title="Номер телефона изменён",
            body=f"Вход в приложение теперь по номеру {req.new_phone}",
            data={"type": "phone_change", "request_id": str(request_id)},
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
            "phone_change_request.reject", "phone_change_request", request_id,
            admin_id, actor_role,
            {"user_id": req.user_id, "new_phone": req.new_phone, "reason": data.admin_response},
        )
        await self.session.commit()

        from app.services.notification_service import NotificationService
        await NotificationService(self.session).send(
            user_id=req.user_id,
            type_="request_status",
            title="Заявка на смену номера отклонена",
            body=data.admin_response,
            data={"type": "phone_change", "request_id": str(request_id)},
        )
