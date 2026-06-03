from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.faq import FaqCategoryOut, FaqEntryOut
from app.schemas.pagination import PageOut
from app.services.faq_service import FaqCategoryService, FaqEntryService

router = APIRouter(prefix="/faq", tags=["faq"])


@router.get("/categories", response_model=list[FaqCategoryOut])
async def list_categories(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await FaqCategoryService(session).list_all()


@router.get("/entries", response_model=PageOut[FaqEntryOut])
async def list_entries(
    category_id: int | None = Query(None, gt=0),
    search: str | None = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("created_at", pattern="^(created_at|updated_at|question)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await FaqEntryService(session).list_entries(category_id, search, sort_by, sort_order, page, size)
