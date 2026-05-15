from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_tag import DocumentTag
from app.models.kb_article_tag import KbArticleTag
from app.models.tag import Tag


class TagRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self) -> list[Tag]:
        result = await self.session.execute(select(Tag).order_by(Tag.name))
        return list(result.scalars().all())

    async def get_by_id(self, tag_id: int) -> Tag | None:
        return await self.session.get(Tag, tag_id)

    async def get_by_name(self, name: str) -> Tag | None:
        result = await self.session.execute(select(Tag).where(Tag.name == name))
        return result.scalar_one_or_none()

    async def create(self, name: str) -> Tag:
        tag = Tag(name=name)
        self.session.add(tag)
        await self.session.flush()
        return tag

    async def delete(self, tag: Tag) -> None:
        await self.session.delete(tag)
        await self.session.flush()

    async def get_tags_for_documents(self, doc_ids: list[int]) -> dict[int, list[Tag]]:
        if not doc_ids:
            return {}
        result = await self.session.execute(
            select(DocumentTag.document_id, Tag)
            .join(Tag, Tag.id == DocumentTag.tag_id)
            .where(DocumentTag.document_id.in_(doc_ids))
        )
        mapping: dict[int, list[Tag]] = {doc_id: [] for doc_id in doc_ids}
        for doc_id, tag in result.all():
            mapping[doc_id].append(tag)
        return mapping

    async def set_document_tags(self, document_id: int, tag_ids: list[int]) -> None:
        await self.session.execute(
            delete(DocumentTag).where(DocumentTag.document_id == document_id)
        )
        for tag_id in tag_ids:
            self.session.add(DocumentTag(document_id=document_id, tag_id=tag_id))
        await self.session.flush()

    async def get_tags_for_articles(self, article_ids: list[int]) -> dict[int, list[Tag]]:
        if not article_ids:
            return {}
        result = await self.session.execute(
            select(KbArticleTag.article_id, Tag)
            .join(Tag, Tag.id == KbArticleTag.tag_id)
            .where(KbArticleTag.article_id.in_(article_ids))
        )
        mapping: dict[int, list[Tag]] = {a_id: [] for a_id in article_ids}
        for article_id, tag in result.all():
            mapping[article_id].append(tag)
        return mapping

    async def set_article_tags(self, article_id: int, tag_ids: list[int]) -> None:
        await self.session.execute(
            delete(KbArticleTag).where(KbArticleTag.article_id == article_id)
        )
        for tag_id in tag_ids:
            self.session.add(KbArticleTag(article_id=article_id, tag_id=tag_id))
        await self.session.flush()

    async def get_document_ids_by_tags(self, tag_ids: list[int]) -> list[int]:
        result = await self.session.execute(
            select(DocumentTag.document_id).where(DocumentTag.tag_id.in_(tag_ids)).distinct()
        )
        return [row[0] for row in result.all()]
