from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.repositories.faq import FaqCategoryRepository, FaqEntryRepository
from app.schemas.faq import (
    FaqCategoryCreateIn,
    FaqCategoryOut,
    FaqCategoryUpdateIn,
    FaqEntryCreateIn,
    FaqEntryOut,
    FaqEntryUpdateIn,
)
from app.schemas.pagination import PageOut, make_page


class FaqCategoryService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = FaqCategoryRepository(session)

    async def create(self, data: FaqCategoryCreateIn) -> FaqCategoryOut:
        cat = await self.repo.create(data.name, data.parent_id, data.sort_order)
        await self.session.commit()
        return FaqCategoryOut.model_validate(cat)

    async def list_all(self) -> list[FaqCategoryOut]:
        cats = await self.repo.list_all()
        return [FaqCategoryOut.model_validate(c) for c in cats]

    async def update(self, cat_id: int, data: FaqCategoryUpdateIn) -> FaqCategoryOut:
        cat = await self.repo.get_by_id(cat_id)
        if cat is None:
            raise NotFoundError("Категория не найдена")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(cat, field, value)
        await self.session.commit()
        return FaqCategoryOut.model_validate(cat)

    async def delete(self, cat_id: int) -> None:
        cat = await self.repo.get_by_id(cat_id)
        if cat is None:
            raise NotFoundError("Категория не найдена")
        await self.repo.delete(cat)
        await self.session.commit()


class FaqEntryService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = FaqEntryRepository(session)

    async def create(self, data: FaqEntryCreateIn) -> FaqEntryOut:
        entry = await self.repo.create(data.category_id, data.question, data.answer)
        await self.session.commit()
        await self.session.refresh(entry)
        return FaqEntryOut.model_validate(entry)

    async def update(self, entry_id: int, data: FaqEntryUpdateIn) -> FaqEntryOut:
        entry = await self.repo.get_by_id(entry_id)
        if entry is None:
            raise NotFoundError("Вопрос не найден")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(entry, field, value)
        entry.version += 1
        await self.session.commit()
        await self.session.refresh(entry)
        return FaqEntryOut.model_validate(entry)

    async def delete(self, entry_id: int) -> None:
        entry = await self.repo.get_by_id(entry_id)
        if entry is None:
            raise NotFoundError("Вопрос не найден")
        await self.repo.delete(entry)
        await self.session.commit()

    async def list_entries(
        self,
        category_id: int | None,
        search: str | None,
        page: int,
        size: int,
    ) -> PageOut[FaqEntryOut]:
        items, total = await self.repo.list_entries(
            category_id, search, offset=(page - 1) * size, limit=size
        )
        return make_page([FaqEntryOut.model_validate(e) for e in items], total, page, size)
