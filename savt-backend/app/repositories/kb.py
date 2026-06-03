import re
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb_article_attachment import KbArticleAttachment
from app.models.kb_article_tag import KbArticleTag
from app.models.kbarticle import KbArticle
from app.models.kbcategory import KbCategory
from app.models.tag import Tag


def _make_slug(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return f"{slug}-{uuid.uuid4().hex[:6]}"


class KbCategoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, slug: str, parent_id: int | None,
                     description: str | None, sort_order: int) -> KbCategory:
        cat = KbCategory(name=name, slug=slug, parent_id=parent_id,
                         description=description, sort_order=sort_order)
        self.session.add(cat)
        await self.session.flush()
        return cat

    async def get_by_id(self, cat_id: int) -> KbCategory | None:
        return await self.session.get(KbCategory, cat_id)

    async def list_all(self) -> list[KbCategory]:
        result = await self.session.execute(
            select(KbCategory).order_by(KbCategory.sort_order, KbCategory.name)
        )
        return list(result.scalars().all())

    async def delete(self, cat: KbCategory) -> None:
        await self.session.delete(cat)
        await self.session.flush()


class KbArticleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, category_id: int, title: str, description: str | None) -> KbArticle:
        article = KbArticle(
            category_id=category_id,
            title=title,
            slug=_make_slug(title),
            content=description,
            is_published=True,
        )
        self.session.add(article)
        await self.session.flush()
        return article

    async def get_by_id(self, article_id: int) -> KbArticle | None:
        return await self.session.get(KbArticle, article_id)

    async def delete(self, article: KbArticle) -> None:
        await self.session.delete(article)
        await self.session.flush()

    async def list_articles(
        self,
        category_id: int | None = None,
        tag_ids: list[int] | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[KbArticle], int]:
        conditions = [KbArticle.is_published == True]

        if category_id is not None:
            conditions.append(KbArticle.category_id == category_id)

        if search:
            conditions.append(or_(
                KbArticle.title.ilike(f"%{search}%"),
                KbArticle.content.ilike(f"%{search}%"),
            ))

        if tag_ids:
            tag_subq = (
                select(KbArticleTag.article_id)
                .where(KbArticleTag.tag_id.in_(tag_ids))
                .distinct()
                .scalar_subquery()
            )
            conditions.append(KbArticle.id.in_(tag_subq))

        total = (await self.session.execute(
            select(func.count(KbArticle.id)).where(*conditions)
        )).scalar() or 0

        _sort_col = {
            "title": KbArticle.title,
            "created_at": KbArticle.created_at,
            "updated_at": KbArticle.updated_at,
        }.get(sort_by, KbArticle.created_at)
        order = _sort_col.asc() if sort_order == "asc" else _sort_col.desc()

        result = await self.session.execute(
            select(KbArticle).where(*conditions)
            .order_by(order)
            .offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def get_tags(self, article_ids: list[int]) -> dict[int, list[Tag]]:
        if not article_ids:
            return {}
        result = await self.session.execute(
            select(KbArticleTag.article_id, Tag)
            .join(Tag, Tag.id == KbArticleTag.tag_id)
            .where(KbArticleTag.article_id.in_(article_ids))
        )
        mapping: dict[int, list[Tag]] = {aid: [] for aid in article_ids}
        for article_id, tag in result.all():
            mapping[article_id].append(tag)
        return mapping

    async def get_attachments(self, article_id: int) -> list[KbArticleAttachment]:
        result = await self.session.execute(
            select(KbArticleAttachment)
            .where(KbArticleAttachment.article_id == article_id)
            .order_by(KbArticleAttachment.created_at)
        )
        return list(result.scalars().all())

    async def get_attachment(self, att_id: int) -> KbArticleAttachment | None:
        return await self.session.get(KbArticleAttachment, att_id)

    async def add_attachment(
        self, article_id: int, file_url: str, file_size_bytes: int,
        doc_type: str, mime_type: str, title: str
    ) -> KbArticleAttachment:
        att = KbArticleAttachment(
            article_id=article_id,
            file_url=file_url,
            file_size_bytes=file_size_bytes,
            doc_type=doc_type,
            mime_type=mime_type,
            title=title,
        )
        self.session.add(att)
        await self.session.flush()
        return att

    async def delete_attachment(self, att: KbArticleAttachment) -> None:
        await self.session.delete(att)
        await self.session.flush()
