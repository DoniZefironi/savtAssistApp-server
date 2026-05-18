from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_category import FaqCategory
from app.models.faq_entry import FaqEntry


class FaqCategoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, parent_id: int | None, sort_order: int) -> FaqCategory:
        cat = FaqCategory(name=name, parent_id=parent_id, sort_order=sort_order)
        self.session.add(cat)
        await self.session.flush()
        return cat

    async def get_by_id(self, cat_id: int) -> FaqCategory | None:
        return await self.session.get(FaqCategory, cat_id)

    async def list_all(self) -> list[FaqCategory]:
        result = await self.session.execute(
            select(FaqCategory).order_by(FaqCategory.sort_order, FaqCategory.name)
        )
        return list(result.scalars().all())

    async def delete(self, cat: FaqCategory) -> None:
        await self.session.delete(cat)
        await self.session.flush()


class FaqEntryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, category_id: int, question: str, answer: str) -> FaqEntry:
        entry = FaqEntry(category_id=category_id, question=question, answer=answer)
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_by_id(self, entry_id: int) -> FaqEntry | None:
        return await self.session.get(FaqEntry, entry_id)

    async def delete(self, entry: FaqEntry) -> None:
        await self.session.delete(entry)
        await self.session.flush()

    async def list_entries(
        self,
        category_id: int | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[FaqEntry], int]:
        conditions = []
        if category_id is not None:
            conditions.append(FaqEntry.category_id == category_id)
        if search:
            conditions.append(FaqEntry.question.ilike(f"%{search}%"))

        count_stmt = select(func.count(FaqEntry.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = select(FaqEntry).order_by(FaqEntry.category_id, FaqEntry.created_at)
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return list(result.scalars().all()), total
