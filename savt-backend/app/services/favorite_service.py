from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.favorite import FavoriteRepository
from app.schemas.favorites import FavoriteIn, FavoriteOut
from app.schemas.pagination import PageOut, make_page


class FavoriteService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = FavoriteRepository(session)

    async def add(self, user_id: int, data: FavoriteIn) -> FavoriteOut:
        existing = await self.repo.find(user_id, data.entity_type, data.entity_id)
        if existing is not None:
            raise AlreadyExistsError("Уже в избранном")
        fav = await self.repo.add(user_id, data.entity_type, data.entity_id)
        await self.session.commit()
        return FavoriteOut.model_validate(fav)

    async def remove(self, user_id: int, entity_type: str, entity_id: int) -> None:
        fav = await self.repo.find(user_id, entity_type, entity_id)
        if fav is None:
            raise NotFoundError("Не найдено в избранном")
        await self.repo.remove(fav)
        await self.session.commit()

    async def list_favorites(
        self,
        user_id: int,
        entity_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> PageOut[FavoriteOut]:
        items, total = await self.repo.list_for_user(
            user_id=user_id,
            entity_type=entity_type,
            offset=(page - 1) * size,
            limit=size,
        )
        return make_page([FavoriteOut.model_validate(f) for f in items], total, page, size)
