from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_current_user, get_session, require_role
from app.models.user import User
from app.schemas.tags import DocumentTagsIn, TagCreateIn, TagOut
from app.services.tag_service import TagService

router = APIRouter(tags=["tags"])


@router.get("/tags", response_model=list[TagOut])
async def list_tags(
    scope: str | None = Query(None, pattern="^(document|cabinet)$"),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await TagService(session).list_all(scope=scope)


@router.post("/admin/tags", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(
    payload: TagCreateIn,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await TagService(session).create(payload)


@router.delete("/admin/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await TagService(session).delete(tag_id)


@router.put("/admin/documents/{doc_id}/tags", status_code=status.HTTP_204_NO_CONTENT)
async def set_document_tags(
    doc_id: int,
    payload: DocumentTagsIn,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    await TagService(session).set_document_tags(doc_id, payload.tag_ids)


@router.put("/admin/kb-articles/{article_id}/tags", status_code=status.HTTP_204_NO_CONTENT)
async def set_article_tags(
    article_id: int,
    payload: DocumentTagsIn,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    await TagService(session).set_article_tags(article_id, payload.tag_ids)
