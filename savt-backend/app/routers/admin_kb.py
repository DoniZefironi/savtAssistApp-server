import asyncio

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.database import AsyncSessionLocal
from app.models.user import User
from app.schemas.kb import (
    KbArticleCreateIn,
    KbArticleDetailOut,
    KbArticleUpdateIn,
    KbAttachmentOut,
    KbCategoryCreateIn,
    KbCategoryOut,
    KbCategoryUpdateIn,
)
from app.services.kb_service import KbArticleService, KbCategoryService


def _reindex_kb(article_id: int) -> None:
    async def _task():
        from app.models.kbarticle import KbArticle
        from app.services.bot_indexer import index_kb_article
        async with AsyncSessionLocal() as s:
            article = await s.get(KbArticle, article_id)
            if article:
                await index_kb_article(s, article)
                await s.commit()
    asyncio.create_task(_task())

router = APIRouter(prefix="/admin/kb", tags=["admin: kb"])


# --- Категории ---

@router.post("/categories", response_model=KbCategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: KbCategoryCreateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await KbCategoryService(session).create(payload)


@router.get("/categories", response_model=list[KbCategoryOut])
async def list_categories(
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await KbCategoryService(session).list_all()


@router.patch("/categories/{cat_id}", response_model=KbCategoryOut)
async def update_category(
    cat_id: int,
    payload: KbCategoryUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await KbCategoryService(session).update(cat_id, payload)


@router.delete("/categories/{cat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    cat_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await KbCategoryService(session).delete(cat_id)


# --- Записи ---

@router.post("/articles", response_model=KbArticleDetailOut, status_code=status.HTTP_201_CREATED)
async def create_article(
    payload: KbArticleCreateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    article = await KbArticleService(session).create(payload)
    _reindex_kb(article.id)
    return article


@router.patch("/articles/{article_id}", response_model=KbArticleDetailOut)
async def update_article(
    article_id: int,
    payload: KbArticleUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    article = await KbArticleService(session).update(article_id, payload)
    _reindex_kb(article.id)
    return article


@router.delete("/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(
    article_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await KbArticleService(session).delete(article_id)


# --- Вложения ---

@router.post("/articles/{article_id}/attachments", response_model=KbAttachmentOut, status_code=status.HTTP_201_CREATED)
async def add_attachment(
    article_id: int,
    file: UploadFile = File(...),
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    att = await KbArticleService(session).add_attachment(article_id, file)
    _reindex_kb(article_id)
    return att


@router.delete("/articles/{article_id}/attachments/{att_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    article_id: int,
    att_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await KbArticleService(session).delete_attachment(article_id, att_id)
    _reindex_kb(article_id)
