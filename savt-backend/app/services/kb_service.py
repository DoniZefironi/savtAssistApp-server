from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.repositories.kb import KbArticleRepository, KbCategoryRepository
from app.schemas.kb import (
    KbArticleCreateIn,
    KbArticleDetailOut,
    KbArticleListOut,
    KbArticleUpdateIn,
    KbAttachmentOut,
    KbCategoryCreateIn,
    KbCategoryOut,
    KbCategoryUpdateIn,
)
from app.schemas.pagination import PageOut, make_page
from app.schemas.tags import TagOut
from app.services.upload_service import UPLOAD_ROOT, save_attachment_with_meta


class KbCategoryService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = KbCategoryRepository(session)

    async def create(self, data: KbCategoryCreateIn) -> KbCategoryOut:
        from app.repositories.kb import _make_slug
        cat = await self.repo.create(
            name=data.name,
            slug=_make_slug(data.name),
            parent_id=data.parent_id,
            description=data.description,
            sort_order=data.sort_order,
        )
        await self.session.commit()
        return KbCategoryOut.model_validate(cat)

    async def list_all(self) -> list[KbCategoryOut]:
        cats = await self.repo.list_all()
        return [KbCategoryOut.model_validate(c) for c in cats]

    async def update(self, cat_id: int, data: KbCategoryUpdateIn) -> KbCategoryOut:
        cat = await self.repo.get_by_id(cat_id)
        if cat is None:
            raise NotFoundError("Категория не найдена")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(cat, field, value)
        await self.session.commit()
        return KbCategoryOut.model_validate(cat)

    async def delete(self, cat_id: int) -> None:
        cat = await self.repo.get_by_id(cat_id)
        if cat is None:
            raise NotFoundError("Категория не найдена")
        await self.repo.delete(cat)
        await self.session.commit()


class KbArticleService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = KbArticleRepository(session)

    async def create(self, data: KbArticleCreateIn) -> KbArticleDetailOut:
        article = await self.repo.create(
            category_id=data.category_id,
            title=data.title,
            description=data.description,
        )
        await self.session.commit()
        await self.session.refresh(article)
        return await self._to_detail(article)

    async def update(self, article_id: int, data: KbArticleUpdateIn) -> KbArticleDetailOut:
        article = await self.repo.get_by_id(article_id)
        if article is None:
            raise NotFoundError("Запись не найдена")
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "description":
                article.content = value
            else:
                setattr(article, field, value)
        await self.session.commit()
        await self.session.refresh(article)
        return await self._to_detail(article)

    async def delete(self, article_id: int) -> None:
        article = await self.repo.get_by_id(article_id)
        if article is None:
            raise NotFoundError("Запись не найдена")
        await self.repo.delete(article)
        await self.session.commit()

    async def list_articles(
        self,
        category_id: int | None,
        tag_ids: list[int] | None,
        search: str | None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[KbArticleListOut]:
        articles, total = await self.repo.list_articles(
            category_id=category_id,
            tag_ids=tag_ids,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=(page - 1) * size,
            limit=size,
        )
        ids = [a.id for a in articles]
        tags_map = await self.repo.get_tags(ids)
        atts_counts = {}
        for a in articles:
            atts = await self.repo.get_attachments(a.id)
            atts_counts[a.id] = len(atts)

        items = [
            KbArticleListOut(
                id=a.id,
                category_id=a.category_id,
                title=a.title,
                slug=a.slug,
                description=a.content,
                created_at=a.created_at,
                tags=[TagOut.model_validate(t) for t in tags_map.get(a.id, [])],
                attachment_count=atts_counts.get(a.id, 0),
            )
            for a in articles
        ]
        return make_page(items, total, page, size)

    async def get_detail(self, article_id: int) -> KbArticleDetailOut:
        article = await self.repo.get_by_id(article_id)
        if article is None or not article.is_published:
            raise NotFoundError("Запись не найдена")
        return await self._to_detail(article)

    async def add_attachment(self, article_id: int, file: UploadFile) -> KbAttachmentOut:
        article = await self.repo.get_by_id(article_id)
        if article is None:
            raise NotFoundError("Запись не найдена")
        info = await save_attachment_with_meta(file)
        title = file.filename or info.doc_type
        att = await self.repo.add_attachment(
            article_id=article_id,
            file_url=info.url,
            file_size_bytes=info.file_size_bytes,
            doc_type=info.doc_type,
            mime_type=info.mime_type,
            title=title,
        )
        await self.session.commit()
        await self.session.refresh(att)
        return KbAttachmentOut.model_validate(att)

    async def delete_attachment(self, article_id: int, att_id: int) -> None:
        att = await self.repo.get_attachment(att_id)
        if att is None or att.article_id != article_id:
            raise NotFoundError("Вложение не найдено")
        await self.repo.delete_attachment(att)
        await self.session.commit()

    async def download_attachment(self, article_id: int, att_id: int) -> tuple[Path, str, str]:
        att = await self.repo.get_attachment(att_id)
        if att is None or att.article_id != article_id:
            raise NotFoundError("Вложение не найдено")
        file_path = UPLOAD_ROOT / att.file_url.removeprefix("/static/")
        return file_path, att.mime_type, att.title

    async def _to_detail(self, article) -> KbArticleDetailOut:
        tags_map = await self.repo.get_tags([article.id])
        atts = await self.repo.get_attachments(article.id)
        return KbArticleDetailOut(
            id=article.id,
            category_id=article.category_id,
            title=article.title,
            slug=article.slug,
            description=article.content,
            version=article.version,
            created_at=article.created_at,
            updated_at=article.updated_at,
            tags=[TagOut.model_validate(t) for t in tags_map.get(article.id, [])],
            attachments=[KbAttachmentOut.model_validate(a) for a in atts],
        )
