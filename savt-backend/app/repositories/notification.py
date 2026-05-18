from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_token import DeviceToken
from app.models.notification import Notification
from app.models.notification_settings import NotificationSettings


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, user_id: int, type_: str, title: str, body: str, data: dict
    ) -> Notification:
        n = Notification(user_id=user_id, type=type_, title=title, body=body, data=data)
        self.session.add(n)
        await self.session.flush()
        return n

    async def get_by_id(self, notif_id: int) -> Notification | None:
        return await self.session.get(Notification, notif_id)

    async def list_for_user(
        self,
        user_id: int,
        is_read: bool | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Notification], int]:
        conditions = [Notification.user_id == user_id]
        if is_read is not None:
            conditions.append(Notification.is_read == is_read)

        total = (await self.session.execute(
            select(func.count(Notification.id)).where(*conditions)
        )).scalar() or 0

        result = await self.session.execute(
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc())
            .offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def mark_read(self, notif_id: int, user_id: int) -> Notification | None:
        n = await self.get_by_id(notif_id)
        if n and n.user_id == user_id:
            n.is_read = True
        return n

    async def mark_all_read(self, user_id: int) -> None:
        from sqlalchemy import update
        await self.session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.is_read == False)
            .values(is_read=True)
        )

    async def get_settings(self, user_id: int) -> NotificationSettings | None:
        return await self.session.get(NotificationSettings, user_id)

    async def ensure_settings(self, user_id: int) -> NotificationSettings:
        settings = await self.get_settings(user_id)
        if settings is None:
            settings = NotificationSettings(user_id=user_id)
            self.session.add(settings)
            await self.session.flush()
        return settings

    async def get_all_user_ids(self, role_name: str | None = None) -> list[int]:
        from app.models.user import User
        from app.models.role import Role
        stmt = select(User.id).where(User.is_active == True)
        if role_name:
            stmt = stmt.join(Role, Role.id == User.role_id).where(Role.name == role_name)
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]


class DeviceTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, user_id: int, token: str, platform: str) -> None:
        existing = await self.session.execute(
            select(DeviceToken).where(DeviceToken.token == token)
        )
        dt = existing.scalar_one_or_none()
        if dt is None:
            self.session.add(DeviceToken(user_id=user_id, token=token, platform=platform))
        else:
            dt.user_id = user_id
            dt.platform = platform
        await self.session.flush()

    async def delete(self, token: str, user_id: int) -> bool:
        result = await self.session.execute(
            select(DeviceToken).where(
                DeviceToken.token == token, DeviceToken.user_id == user_id
            )
        )
        dt = result.scalar_one_or_none()
        if dt:
            await self.session.delete(dt)
            return True
        return False
