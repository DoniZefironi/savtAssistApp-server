from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_category import FaqCategory
from app.models.faq_entry import FaqEntry
from app.utils.db import fuzzy_condition


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

    async def list_all(
        self,
        search: str | None = None,
        parent_id: int | None = None,
        sort_by: str = "sort_order",
        sort_order: str = "asc",
    ) -> list[FaqCategory]:
        conditions = []
        if search:
            conditions.append(fuzzy_condition(search, FaqCategory.name))
        if parent_id is not None:
            conditions.append(FaqCategory.parent_id == parent_id)

        _sort_col = {
            "name": FaqCategory.name,
            "sort_order": FaqCategory.sort_order,
        }.get(sort_by, FaqCategory.sort_order)
        order = _sort_col.asc() if sort_order == "asc" else _sort_col.desc()

        stmt = select(FaqCategory)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(order, FaqCategory.name)
        result = await self.session.execute(stmt)
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
        is_published: bool | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[FaqEntry], int]:
        conditions = []
        if category_id is not None:
            conditions.append(FaqEntry.category_id == category_id)
        if is_published is not None:
            conditions.append(FaqEntry.is_published == is_published)
        if search:
            conditions.append(fuzzy_condition(search, FaqEntry.question, FaqEntry.answer))

        count_stmt = select(func.count(FaqEntry.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        _sort_col = {
            "question": FaqEntry.question,
            "created_at": FaqEntry.created_at,
            "updated_at": FaqEntry.updated_at,
            "version": FaqEntry.version,
            "is_published": FaqEntry.is_published,
        }.get(sort_by, FaqEntry.created_at)
        order = _sort_col.asc() if sort_order == "asc" else _sort_col.desc()

        stmt = select(FaqEntry)
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.order_by(order).offset(offset).limit(limit))
        return list(result.scalars().all()), total
