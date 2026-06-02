from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role
from app.models.user import User
from app.repositories.base import BaseRepository


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
        role: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list, int]:
        # системные роли не показываем
        _hidden = ["admin", "bot"]
        conditions = [~Role.name.in_(_hidden)]

        if query:
            conditions.append(or_(
                User.full_name.ilike(f"%{query}%"),
                User.phone.ilike(f"%{query}%"),
                User.organization_name.ilike(f"%{query}%"),
            ))
        if is_active is not None:
            conditions.append(User.is_active == is_active)
        if role in ("user", "operator"):
            conditions.append(Role.name == role)

        # Роли: operator выводится первым, потом user
        _role_order = case({"operator": 0, "user": 1}, value=Role.name, else_=2)

        _sort_map = {
            "created_at": (User.created_at.desc() if sort_order == "desc" else User.created_at.asc()),
            "full_name":  (User.full_name.asc()    if sort_order == "asc"  else User.full_name.desc()),
            "role":       (_role_order.asc()        if sort_order == "asc"  else _role_order.desc()),
        }
        order_col = _sort_map.get(sort_by, User.created_at.desc())

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