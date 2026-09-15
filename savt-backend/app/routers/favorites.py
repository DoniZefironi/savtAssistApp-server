from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.favorites import FavoriteIn, FavoriteOut
from app.schemas.pagination import PageOut
from app.services.favorite_service import FavoriteService

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.post("", response_model=FavoriteOut, status_code=status.HTTP_201_CREATED)
async def add_favorite(
    payload: FavoriteIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await FavoriteService(session).add(current_user.id, payload)


@router.delete("/{entity_type}/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    entity_type: str,
    entity_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await FavoriteService(session).remove(current_user.id, entity_type, entity_id)


@router.get("", response_model=PageOut[FavoriteOut])
async def list_favorites(
    entity_type: str | None = Query(None, pattern="^(document|kb_article|faq_entry)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await FavoriteService(session).list_favorites(
        user_id=current_user.id, entity_type=entity_type, page=page, size=size
    )
