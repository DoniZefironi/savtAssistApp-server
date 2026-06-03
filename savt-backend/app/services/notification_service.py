from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.repositories.notification import DeviceTokenRepository, NotificationRepository
from app.services.audit_service import AuditLogger
from app.schemas.notifications import (
    BroadcastIn,
    DeviceTokenIn,
    NotificationOut,
    NotificationSettingsOut,
    NotificationSettingsPatchIn,
)
from app.schemas.pagination import PageOut, make_page
from app.services.push_service import send_push


class NotificationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = NotificationRepository(session)
        self.device_repo = DeviceTokenRepository(session)
        self.audit = AuditLogger(session)

    async def send(
        self,
        user_id: int,
        type_: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> None:
        settings = await self.repo.get_settings(user_id)

        # Проверяем настройки пользователя
        if settings:
            allowed = {
                "chat_message": settings.chat_messages,
                "request_status": settings.request_status_change,
                "warranty_expiring": settings.warranty_expiring,
                "promotional": settings.promotional,
            }
            if not allowed.get(type_, True):
                return

        notif = await self.repo.create(
            user_id=user_id,
            type_=type_,
            title=title,
            body=body,
            data=data or {},
        )
        await self.session.commit()

        # Push
        await send_push(self.session, user_id, title, body, data)

    async def list_notifications(
        self,
        user_id: int,
        is_read: bool | None,
        page: int,
        size: int,
    ) -> PageOut[NotificationOut]:
        items, total = await self.repo.list_for_user(
            user_id, is_read, offset=(page - 1) * size, limit=size
        )
        return make_page([NotificationOut.model_validate(n) for n in items], total, page, size)

    async def mark_read(self, user_id: int, notif_id: int) -> None:
        n = await self.repo.mark_read(notif_id, user_id)
        if n is None:
            raise NotFoundError("Уведомление не найдено")
        await self.session.commit()

    async def mark_all_read(self, user_id: int) -> None:
        await self.repo.mark_all_read(user_id)
        await self.session.commit()

    async def get_settings(self, user_id: int) -> NotificationSettingsOut:
        settings = await self.repo.ensure_settings(user_id)
        await self.session.commit()
        return NotificationSettingsOut.model_validate(settings)

    async def update_settings(
        self, user_id: int, data: NotificationSettingsPatchIn
    ) -> NotificationSettingsOut:
        settings = await self.repo.ensure_settings(user_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(settings, field, value)
        await self.session.commit()
        return NotificationSettingsOut.model_validate(settings)

    async def register_device(self, user_id: int, data: DeviceTokenIn) -> None:
        await self.device_repo.upsert(user_id, data.token, data.platform)
        await self.session.commit()

    async def remove_device(self, user_id: int, token: str) -> None:
        removed = await self.device_repo.delete(token, user_id)
        if not removed:
            raise NotFoundError("Токен не найден")
        await self.session.commit()

    async def broadcast(self, data: BroadcastIn, actor_id: int = 0, actor_role: str = "admin") -> None:
        user_ids = await self.repo.get_all_user_ids(data.role)
        for user_id in user_ids:
            await self.repo.create(
                user_id=user_id,
                type_="promotional",
                title=data.title,
                body=data.body,
                data={},
            )
        self.audit.log("notification.broadcast", "notification", None, actor_id, actor_role,
                       {"title": data.title, "role": data.role, "recipients": len(user_ids)})
        await self.session.commit()

        for user_id in user_ids:
            await send_push(self.session, user_id, data.title, data.body)
