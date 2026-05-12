from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.cabinet import CabinetRepository, CabinetRequestRepository, UserCabinetRepository
from app.schemas.requests import (
    AdditionRequestOut,
    ApproveAdditionIn,
    ApproveShareIn,
    RejectRequestIn,
    ShareRequestOut,
)


class CabinetRequestService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.request_repo = CabinetRequestRepository(session)
        self.cabinet_repo = CabinetRepository(session)
        self.user_cabinet_repo = UserCabinetRepository(session)

    # Все заявки на добавление по фото
    async def list_additions(self, status: str | None = None) -> list[AdditionRequestOut]:
        rows = await self.request_repo.list_additions(status)
        return [
            AdditionRequestOut(
                id=req.id,
                user_id=req.user_id,
                user_full_name=user.full_name,
                user_phone=user.phone,
                photo_url=req.photo_url,
                user_comment=req.user_comment,
                status=req.status,
                cabinet_id=req.cabinet_id,
                admin_response=req.admin_response,
                created_at=req.created_at,
                resolved_at=req.resolved_at,
            )
            for req, user in rows
        ]

    # Апрув заявки
    async def approve_addition(
        self, request_id: int, data: ApproveAdditionIn, admin_id: int
    ) -> None:
        req = await self.request_repo.get_addition(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        cabinet = await self.cabinet_repo.get_by_id(data.cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")

        existing = await self.user_cabinet_repo.find(req.user_id, data.cabinet_id)
        if existing is not None:
            raise AlreadyExistsError("Пользователь уже привязан к этому ШУ")

        await self.user_cabinet_repo.create(
            user_id=req.user_id,
            cabinet_id=data.cabinet_id,
            is_primary=True,
        )

        req.status = "approved"
        req.cabinet_id = data.cabinet_id
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        await self.session.commit()

    # Не апрув заявки
    async def reject_addition(
        self, request_id: int, data: RejectRequestIn, admin_id: int
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

        await self.session.commit()

    # Все заявки на добавление
    async def list_shares(self, status: str | None = None) -> list[ShareRequestOut]:
        rows = await self.request_repo.list_shares(status)
        return [
            ShareRequestOut(
                id=req.id,
                user_id=req.user_id,
                user_full_name=user.full_name,
                user_phone=user.phone,
                cabinet_id=req.cabinet_id,
                cabinet_type=cabinet.type,
                cabinet_object_number=cabinet.object_number,
                user_comment=req.user_comment,
                status=req.status,
                admin_response=req.admin_response,
                created_at=req.created_at,
                resolved_at=req.resolved_at,
            )
            for req, user, cabinet in rows
        ]

    # Апрув заявки
    async def approve_share(
        self, request_id: int, data: ApproveShareIn, admin_id: int
    ) -> None:
        req = await self.request_repo.get_share(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")

        existing = await self.user_cabinet_repo.find(req.user_id, req.cabinet_id)
        if existing is not None:
            raise AlreadyExistsError("Пользователь уже привязан к этому ШУ")

        await self.user_cabinet_repo.create(
            user_id=req.user_id,
            cabinet_id=req.cabinet_id,
            is_primary=False,
        )

        req.status = "approved"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)

        await self.session.commit()

    # Не апрув заявки
    async def reject_share(
        self, request_id: int, data: RejectRequestIn, admin_id: int
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

        await self.session.commit()
