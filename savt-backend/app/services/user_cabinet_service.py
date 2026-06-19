from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.chat import Chat
from app.models.message import Message
from app.repositories.cabinet import CabinetRepository, CabinetRequestRepository, UserCabinetRepository
from app.schemas.cabinet import UserCabinetDetailOut, UserCabinetListItemOut, UserCabinetPatchIn

# Подсчет статуса гарантии
def _warranty_status(ends_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    if ends_at < now:
        return "expired"
    if ends_at < now + timedelta(days=30):
        return "expiring_soon"
    return "active"


class UserCabinetService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cabinet_repo = CabinetRepository(session)
        self.user_cabinet_repo = UserCabinetRepository(session)
        self.request_repo = CabinetRequestRepository(session)

    # Список ШУ
    async def list_cabinets(self, user_id: int) -> list[UserCabinetListItemOut]:
        rows = await self.user_cabinet_repo.list_for_user(user_id)
        if not rows:
            return []

        cabinet_ids = [uc.cabinet_id for uc, _ in rows]
        unread = await self._get_unread_counts(user_id, cabinet_ids)

        return [
            UserCabinetListItemOut(
                cabinet_id=cab.id,
                type=cab.type,
                object_number=cab.object_number,
                warranty_ends_at=cab.warranty_ends_at,
                warranty_status=_warranty_status(cab.warranty_ends_at),
                custom_name=uc.custom_name or cab.admin_internal_name or cab.object_number,
                is_primary=uc.is_primary,
                unread_count=unread.get(cab.id, 0),
            )
            for uc, cab in rows
        ]

    # Получение ШУ(одного блин)
    async def get_cabinet(self, user_id: int, cabinet_id: int) -> UserCabinetDetailOut:
        row = await self.user_cabinet_repo.get_with_cabinet(user_id, cabinet_id)
        if row is None:
            raise NotFoundError("ШУ не найден")
        uc, cab = row
        return UserCabinetDetailOut(
            cabinet_id=cab.id,
            type=cab.type,
            object_number=cab.object_number,
            description=cab.description,
            purpose=cab.purpose,
            warranty_starts_at=cab.warranty_starts_at,
            warranty_ends_at=cab.warranty_ends_at,
            warranty_status=_warranty_status(cab.warranty_ends_at),
            latitude=cab.latitude,
            longitude=cab.longitude,
            custom_name=uc.custom_name or cab.admin_internal_name or cab.object_number,
            custom_comment=uc.custom_comment,
            is_primary=uc.is_primary,
        )

    # Обновление ШУ
    async def update_cabinet(
        self, user_id: int, cabinet_id: int, data: UserCabinetPatchIn
    ) -> UserCabinetDetailOut:
        uc = await self.user_cabinet_repo.find(user_id, cabinet_id)
        if uc is None:
            raise NotFoundError("ШУ не найден")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(uc, field, value)
        await self.session.commit()
        return await self.get_cabinet(user_id, cabinet_id)

    # Удаление ШУ
    async def remove_cabinet(self, user_id: int, cabinet_id: int) -> None:
        uc = await self.user_cabinet_repo.find(user_id, cabinet_id)
        if uc is None:
            raise NotFoundError("ШУ не найден")
        await self.user_cabinet_repo.delete(uc)
        await self.session.commit()

    # Добавление ШУ по кур-коду
    async def add_by_qr(self, user_id: int, unique_code: str) -> dict:
        cabinet = await self.cabinet_repo.find_by_code(unique_code)
        if cabinet is None:
            raise NotFoundError("ШУ с таким кодом не найден")

        existing = await self.user_cabinet_repo.find(user_id, cabinet.id)
        if existing is not None:
            raise AlreadyExistsError("Этот ШУ уже привязан к вашему аккаунту")

        has_primary = await self.user_cabinet_repo.has_primary(cabinet.id)

        if not has_primary:
            await self.user_cabinet_repo.create(
                user_id=user_id,
                cabinet_id=cabinet.id,
                is_primary=True,
            )
            from app.services.chat_service import ChatService
            await ChatService(self.session).ensure_cabinet_chat(user_id, cabinet.id)
            await self.session.commit()
            return {"status": "linked", "message": "ШУ успешно привязан"}

        pending = await self.request_repo.find_pending_share(user_id, cabinet.id)
        if pending is not None:
            raise AlreadyExistsError("Заявка на доступ к этому ШУ уже отправлена")

        await self.request_repo.create_share(user_id=user_id, cabinet_id=cabinet.id)
        await self.session.commit()
        return {"status": "request_submitted", "message": "Заявка отправлена администратору на рассмотрение"}

    # Добавление ШУ по фото
    async def add_by_photo(self, user_id: int, photo_url: str, user_comment: str | None) -> int:
        pending = await self.request_repo.find_pending_addition(user_id)
        if pending is not None:
            raise AlreadyExistsError("У вас уже есть необработанная заявка на добавление ШУ")

        request = await self.request_repo.create_addition(
            user_id=user_id,
            photo_url=photo_url,
            user_comment=user_comment,
        )
        await self.session.commit()
        return request.id

    # Получение кол-ва непрочитанных сообщений в чате ШУ
    async def _get_unread_counts(self, user_id: int, cabinet_ids: list[int]) -> dict[int, int]:
        result = await self.session.execute(
            select(Chat.cabinet_id, func.count(Message.id))
            .join(Message, Message.chat_id == Chat.id)
            .where(
                Chat.user_id == user_id,
                Chat.cabinet_id.in_(cabinet_ids),
                Message.is_read == False,
                Message.sender_id != user_id,
                Message.deleted_at.is_(None),
            )
            .group_by(Chat.cabinet_id)
        )
        return {cabinet_id: count for cabinet_id, count in result.all()}
