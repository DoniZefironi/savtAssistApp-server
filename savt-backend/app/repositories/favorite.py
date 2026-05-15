from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_favorite import UserFavorite


class FavoriteRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find(self, user_id: int, entity_type: str, entity_id: int) -> UserFavorite | None:
        result = await self.session.execute(
            select(UserFavorite).where(
                UserFavorite.user_id == user_id,
                UserFavorite.entity_type == entity_type,
                UserFavorite.entity_id == entity_id,
            )
        )
        return result.scalar_one_or_none()

    async def add(self, user_id: int, entity_type: str, entity_id: int) -> UserFavorite:
        fav = UserFavorite(user_id=user_id, entity_type=entity_type, entity_id=entity_id)
        self.session.add(fav)
        await self.session.flush()
        return fav

    async def remove(self, fav: UserFavorite) -> None:
        await self.session.delete(fav)
        await self.session.flush()

    async def list_for_user(
        self,
        user_id: int,
        entity_type: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[UserFavorite], int]:
        conditions = [UserFavorite.user_id == user_id]
        if entity_type:
            conditions.append(UserFavorite.entity_type == entity_type)

        count_stmt = select(func.count(UserFavorite.id)).where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(UserFavorite)
            .where(*conditions)
            .order_by(UserFavorite.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total
