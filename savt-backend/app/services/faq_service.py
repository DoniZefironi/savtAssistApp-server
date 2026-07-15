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

    async def list_all(
        self,
        search: str | None = None,
        parent_id: int | None = None,
        sort_by: str = "sort_order",
        sort_order: str = "asc",
    ) -> list[FaqCategoryOut]:
        cats = await self.repo.list_all(search, parent_id, sort_by, sort_order)
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
        from sqlalchemy import delete as sa_delete, select
        from app.models.embedding import Embedding
        from app.models.faq_entry import FaqEntry

        cat = await self.repo.get_by_id(cat_id)
        if cat is None:
            raise NotFoundError("Категория не найдена")

        # Вопросы категории удалятся каскадом на уровне БД (ondelete=CASCADE) —
        # это в обход FaqEntryService.delete(), который обычно чистит embeddings
        # сам, поэтому чистим их здесь заранее.
        entry_ids = (await self.session.execute(
            select(FaqEntry.id).where(FaqEntry.category_id == cat_id)
        )).scalars().all()
        if entry_ids:
            await self.session.execute(
                sa_delete(Embedding).where(
                    Embedding.source_type == "faq",
                    Embedding.source_id.in_(entry_ids),
                )
            )

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
        from sqlalchemy import delete as sa_delete
        from app.models.embedding import Embedding
        entry = await self.repo.get_by_id(entry_id)
        if entry is None:
            raise NotFoundError("Вопрос не найден")
        await self.session.execute(
            sa_delete(Embedding).where(
                Embedding.source_type == "faq",
                Embedding.source_id == entry_id,
            )
        )
        await self.repo.delete(entry)
        await self.session.commit()

    async def list_entries(
        self,
        category_id: int | None,
        search: str | None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
        is_published: bool | None = None,
    ) -> PageOut[FaqEntryOut]:
        items, total = await self.repo.list_entries(
            category_id, is_published, search, sort_by, sort_order,
            offset=(page - 1) * size, limit=size,
        )
        return make_page([FaqEntryOut.model_validate(e) for e in items], total, page, size)
