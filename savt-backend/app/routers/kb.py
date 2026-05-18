from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.kb import KbArticleDetailOut, KbArticleListOut, KbCategoryOut
from app.schemas.pagination import PageOut
from app.services.kb_service import KbArticleService, KbCategoryService

router = APIRouter(prefix="/kb", tags=["kb"])


@router.get("/categories", response_model=list[KbCategoryOut])
async def list_categories(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await KbCategoryService(session).list_all()


@router.get("/articles", response_model=PageOut[KbArticleListOut])
async def list_articles(
    category_id: int | None = Query(None, gt=0),
    tag_ids: list[int] = Query(default=[]),
    search: str | None = Query(None, min_length=1, max_length=200),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await KbArticleService(session).list_articles(
        category_id=category_id,
        tag_ids=tag_ids or None,
        search=search,
        page=page,
        size=size,
    )


@router.get("/articles/{article_id}", response_model=KbArticleDetailOut)
async def get_article(
    article_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await KbArticleService(session).get_detail(article_id)


@router.get("/articles/{article_id}/attachments/{att_id}/download")
async def download_attachment(
    article_id: int,
    att_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    file_path, mime_type, title = await KbArticleService(session).download_attachment(
        article_id, att_id
    )
    return FileResponse(path=str(file_path), media_type=mime_type, filename=title)
