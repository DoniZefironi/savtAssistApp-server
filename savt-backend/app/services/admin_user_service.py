from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.audit_log import AuditLog
from app.models.role import Role
from app.repositories.cabinet import CabinetRepository, UserCabinetRepository
from app.repositories.user import UserRepository
from app.schemas.admin_users import (
    AdminUserCabinetItem,
    AdminUserDetailOut,
    AdminUserListOut,
    CabinetUserOut,
    CreateAdminIn,
    CreateOperatorIn,
)
from app.schemas.pagination import PageOut, make_page
from app.utils.warranty import warranty_status as _warranty_status


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
        is_verified: bool | None = None,
        is_phone_verified: bool | None = None,
        user_type: str | None = None,
        role: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[AdminUserListOut]:
        rows, total = await self.user_repo.admin_search(
            query=query, is_active=is_active,
            is_verified=is_verified, is_phone_verified=is_phone_verified,
            user_type=user_type, role=role, sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size,
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
    async def get_user_detail(
        self, user_id: int, allowed_roles: tuple = ("user", "operator")
    ) -> AdminUserDetailOut:
        row = await self.user_repo.get_with_role(user_id)
        if row is None:
            raise NotFoundError("Пользователь не найден")
        user, role = row
        if role.name not in allowed_roles:
            raise NotFoundError("Пользователь не найден")

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

    # Создание администратора (только суперадмин)
    async def create_admin(self, data: CreateAdminIn, actor_id: int, actor_role: str) -> AdminUserListOut:
        from app.core.exceptions import AlreadyExistsError
        from app.core.security import hash_password
        from app.models.role import Role
        from sqlalchemy import select

        existing = await self.user_repo.find_by_login(data.login)
        if existing is not None:
            raise AlreadyExistsError("Пользователь с таким логином уже существует")

        role = (await self.session.execute(
            select(Role).where(Role.name == "admin")
        )).scalar_one_or_none()
        if role is None:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("Роль 'admin' не найдена")

        user = await self.user_repo.create(
            login=data.login,
            full_name=data.full_name,
            hashed_password=hash_password(data.password),
            role_id=role.id,
            is_active=True,
            is_phone_verified=True,
            is_verified=True,
        )
        await self._log(actor_id, actor_role, "user.create_admin", "user", user.id, {"login": data.login})
        await self.session.commit()

        return AdminUserListOut(
            id=user.id,
            phone=user.phone,
            login=user.login,
            full_name=user.full_name,
            user_type=user.user_type,
            organization_name=user.organization_name,
            role="admin",
            is_active=user.is_active,
            is_phone_verified=user.is_phone_verified,
            is_verified=user.is_verified,
            created_at=user.created_at,
        )

    # Создание оператора
    async def create_operator(self, data: CreateOperatorIn, actor_id: int, actor_role: str) -> AdminUserListOut:
        from app.core.exceptions import AlreadyExistsError
        from app.core.security import hash_password
        from app.models.role import Role
        from sqlalchemy import select

        existing = await self.user_repo.find_by_login(data.login)
        if existing is not None:
            raise AlreadyExistsError("Пользователь с таким логином уже существует")

        role = (await self.session.execute(
            select(Role).where(Role.name == "operator")
        )).scalar_one_or_none()
        if role is None:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("Роль 'operator' не найдена")

        user = await self.user_repo.create(
            login=data.login,
            full_name=data.full_name,
            hashed_password=hash_password(data.password),
            role_id=role.id,
            is_active=True,
            is_phone_verified=True,
            is_verified=True,
        )
        await self._log(actor_id, actor_role, "user.create_operator", "user", user.id, {"login": data.login})
        await self.session.commit()

        return AdminUserListOut(
            id=user.id,
            phone=user.phone,
            login=user.login,
            full_name=user.full_name,
            user_type=user.user_type,
            organization_name=user.organization_name,
            role="operator",
            is_active=user.is_active,
            is_phone_verified=user.is_phone_verified,
            is_verified=user.is_verified,
            created_at=user.created_at,
        )

    # Удаление оператора (soft permanent delete)
    async def delete_operator(self, user_id: int, actor_id: int, actor_role: str) -> None:
        from datetime import datetime, timezone
        from sqlalchemy import update
        from app.models.role import Role
        from app.models.refresh_token import RefreshToken
        from app.core.exceptions import PermissionDeniedError

        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")

        role = await self.session.get(Role, user.role_id)
        if role is None or role.name != "operator":
            raise PermissionDeniedError("Можно удалять только операторов")

        # Отзываем все сессии
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )

        # Анонимизируем и деактивируем
        user.is_active = False
        user.login = f"_deleted_{user_id}"
        user.hashed_password = "DELETED"
        user.full_name = None
        user.email = None

        await self._log(actor_id, actor_role, "user.delete_operator", "user", user_id, {})
        await self.session.commit()

    # Бан пользователя
    async def ban_user(self, user_id: int, reason: str, actor_id: int, actor_role: str) -> None:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        await self._ensure_target_is_manageable(user)
        user.is_active = False
        await self._log(actor_id, actor_role, "user.ban", "user", user_id, {"reason": reason})
        await self.session.commit()

    # Разбан пользователя
    async def unban_user(self, user_id: int, actor_id: int, actor_role: str) -> None:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        await self._ensure_target_is_manageable(user)
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
        await self._ensure_target_is_manageable(user)
        user.is_verified = True
        await self._log(actor_id, actor_role, "user.verify", "user", user_id, {})
        await self.session.commit()

    # Снять подтверждение
    async def unverify_user(self, user_id: int, actor_id: int, actor_role: str) -> None:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Пользователь не найден")
        await self._ensure_target_is_manageable(user)
        user.is_verified = False
        await self._log(actor_id, actor_role, "user.unverify", "user", user_id, {})
        await self.session.commit()

    # Запрещаем действия над администраторами/суперадминами/системными аккаунтами
    async def _ensure_target_is_manageable(self, user) -> None:
        role = await self.session.get(Role, user.role_id)
        if role is None or role.name not in ("user", "operator"):
            raise PermissionDeniedError("Действие недоступно для этой роли пользователя")

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
