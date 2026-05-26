from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.audit_log import AuditLog
from app.repositories.cabinet import CabinetRepository, UserCabinetRepository
from app.repositories.user import UserRepository
from app.schemas.admin_users import (
    AdminUserCabinetItem,
    AdminUserDetailOut,
    AdminUserListOut,
    CabinetUserOut,
)
from app.schemas.pagination import PageOut, make_page

# Расчет статуса
def _warranty_status(ends_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    if ends_at < now:
        return "expired"
    if ends_at < now + timedelta(days=30):
        return "expiring_soon"
    return "active"


class AdminUserService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self.user_cabinet_repo = UserCabinetRepository(session)
        self.cabinet_repo = CabinetRepository(session)

    # Список пользователей
    async def list_users(
        self,
        query: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> PageOut[AdminUserListOut]:
        rows, total = await self.user_repo.admin_search(
            query=query, is_active=is_active, offset=(page - 1) * size, limit=size
        )
        items = [
            AdminUserListOut(
                id=user.id,
                phone=user.phone,
                login=user.login,
                full_name=user.full_name,
                user_type=user.user_type,
                organization_name=user.organization_name,
                role=role.name,
                is_active=user.is_active,
                is_phone_verified=user.is_phone_verified,
                is_verified=user.is_verified,
                created_at=user.created_at,
            )
            for user, role in rows
        ]
        return make_page(items, total, page, size)

    # Получение детальной инфы о пользователе
    async def get_user_detail(self, user_id: int) -> AdminUserDetailOut:
        row = await self.user_repo.get_with_role(user_id)
        if row is None:
            raise NotFoundError("Пользователь не найден")
        user, role = row

        cabinet_rows = await self.user_cabinet_repo.list_for_user(user_id)
        cabinets = [
            AdminUserCabinetItem(
                cabinet_id=cab.id,
                type=cab.type,
                object_number=cab.object_number,
                warranty_ends_at=cab.warranty_ends_at,
                warranty_status=_warranty_status(cab.warranty_ends_at),
                custom_name=uc.custom_name or cab.admin_internal_name or cab.object_number,
                is_primary=uc.is_primary,
                added_at=uc.added_at,
            )
            for uc, cab in cabinet_rows
        ]

        return AdminUserDetailOut(
            id=user.id,
            phone=user.phone,
            login=user.login,
            full_name=user.full_name,
            email=user.email,
            user_type=user.user_type,
            organization_name=user.organization_name,
            role=role.name,
            is_active=user.is_active,
            is_phone_verified=user.is_phone_verified,
            is_verified=user.is_verified,
            created_at=user.created_at,
            cabinets=cabinets,
        )

    # Бан пользователя
    async def ban_user(self, user_id: int, reason: str, actor_id: int, actor_role: str) -> None:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        user.is_active = False
        await self._log(actor_id, actor_role, "user.ban", "user", user_id, {"reason": reason})
        await self.session.commit()

    # Разбан пользователя
    async def unban_user(self, user_id: int, actor_id: int, actor_role: str) -> None:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        user.is_active = True
        await self._log(actor_id, actor_role, "user.unban", "user", user_id, {})
        await self.session.commit()

    # Все пользователи у шкафа
    async def list_cabinet_users(self, cabinet_id: int) -> list[CabinetUserOut]:
        cabinet = await self.cabinet_repo.get_by_id(cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")
        rows = await self.user_cabinet_repo.list_cabinet_users(cabinet_id)
        return [
            CabinetUserOut(
                user_id=user.id,
                full_name=user.full_name,
                phone=user.phone,
                user_type=user.user_type,
                is_primary=uc.is_primary,
                custom_name=uc.custom_name or cabinet.admin_internal_name or cabinet.object_number,
                added_at=uc.added_at,
            )
            for uc, user in rows
        ]

    # удаление пользователя с ШУ
    async def remove_user_from_cabinet(
        self, cabinet_id: int, user_id: int, reason: str, actor_id: int, actor_role: str
    ) -> None:
        uc = await self.user_cabinet_repo.find(user_id, cabinet_id)
        if uc is None:
            raise NotFoundError("Пользователь не привязан к этому ШУ")
        await self.user_cabinet_repo.delete(uc)
        await self._log(
            actor_id, actor_role, "user_cabinet.remove", "user_cabinet", uc.id,
            {"user_id": user_id, "cabinet_id": cabinet_id, "reason": reason},
        )
        await self.session.commit()

    # Подтвердить аккаунт
    async def verify_user(self, user_id: int, actor_id: int, actor_role: str) -> None:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        user.is_verified = True
        await self._log(actor_id, actor_role, "user.verify", "user", user_id, {})
        await self.session.commit()

    # Снять подтверждение
    async def unverify_user(self, user_id: int, actor_id: int, actor_role: str) -> None:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        user.is_verified = False
        await self._log(actor_id, actor_role, "user.unverify", "user", user_id, {})
        await self.session.commit()

    # Лог
    async def _log(
        self, actor_id: int, actor_role: str, action: str, entity_type: str, entity_id: int, payload: dict
    ) -> None:
        self.session.add(AuditLog(
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        ))
