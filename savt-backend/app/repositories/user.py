from sqlalchemy import func, or_, select
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
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list, int]:
        conditions = []
        if query:
            conditions.append(or_(
                User.full_name.ilike(f"%{query}%"),
                User.phone.ilike(f"%{query}%"),
                User.organization_name.ilike(f"%{query}%"),
            ))
        if is_active is not None:
            conditions.append(User.is_active == is_active)

        count_stmt = select(func.count(User.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = select(User, Role).join(Role, Role.id == User.role_id)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(User.created_at.desc()).offset(offset).limit(limit)
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