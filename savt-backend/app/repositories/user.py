from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role
from app.models.user import User
from app.repositories.base import BaseRepository
from app.utils.db import fuzzy_condition


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession):
        super().__init__(User, session)

    # поиск телефона
    async def find_by_phone(self, phone: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.phone == phone)
        )
        return result.scalar_one_or_none()
    # поиск логина
    async def find_by_login(self, login: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.login == login)
        )
        return result.scalar_one_or_none()
    # админский поиск пользователя
    async def admin_search(
        self,
        query: str | None = None,
        is_active: bool | None = None,
        is_verified: bool | None = None,
        is_phone_verified: bool | None = None,
        user_type: str | None = None,
        role: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list, int]:
        # bot всегда скрыт; superadmin всегда скрыт в общем списке
        # admin виден только при явном role=admin или role=superadmin
        _always_hidden = ["bot", "superadmin"]
        if role in ("admin", "superadmin"):
            conditions = [Role.name == role]
        else:
            _hidden = _always_hidden + (["admin"] if role not in ("user", "operator") else [])
            conditions = [~Role.name.in_(_hidden)]
            if role in ("user", "operator"):
                conditions.append(Role.name == role)

        # Удалённые операторы имеют логин вида _deleted_N — скрываем их
        # NULL login (пользователи по телефону) тоже включаем
        conditions.append(
            or_(User.login.is_(None), ~User.login.ilike("_deleted_%"))
        )

        if query:
            conditions.append(fuzzy_condition(
                query,
                User.full_name, User.phone, User.login, User.email, User.organization_name,
            ))
        if is_active is not None:
            conditions.append(User.is_active == is_active)
        if is_verified is not None:
            conditions.append(User.is_verified == is_verified)
        if is_phone_verified is not None:
            conditions.append(User.is_phone_verified == is_phone_verified)
        if user_type is not None:
            conditions.append(User.user_type == user_type)

        _role_order = case({"operator": 0, "user": 1, "admin": 2}, value=Role.name, else_=3)
        _col = {
            "created_at": User.created_at,
            "full_name":  User.full_name,
            "phone":      User.phone,
            "email":      User.email,
            "login":      User.login,
            "organization_name": User.organization_name,
        }
        if sort_by in _col:
            order_col = _col[sort_by].asc() if sort_order == "asc" else _col[sort_by].desc()
        elif sort_by == "role":
            order_col = _role_order.asc() if sort_order == "asc" else _role_order.desc()
        else:
            order_col = User.created_at.desc()

        count_stmt = (
            select(func.count(User.id))
            .join(Role, Role.id == User.role_id)
            .where(*conditions)
        )
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(User, Role)
            .join(Role, Role.id == User.role_id)
            .where(*conditions)
            .order_by(order_col)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all(), total
    # 
    async def get_with_role(self, user_id: int) -> tuple | None:
        result = await self.session.execute(
            select(User, Role)
            .join(Role, Role.id == User.role_id)
            .where(User.id == user_id)
        )
        return result.one_or_none()