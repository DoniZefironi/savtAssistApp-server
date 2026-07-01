import asyncio

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.database import AsyncSessionLocal
from app.models.user import User
from app.schemas.faq import (
    FaqCategoryCreateIn,
    FaqCategoryOut,
    FaqCategoryUpdateIn,
    FaqEntryCreateIn,
    FaqEntryOut,
    FaqEntryUpdateIn,
)
from app.schemas.pagination import PageOut
from app.services.faq_service import FaqCategoryService, FaqEntryService


def _reindex_faq(entry_id: int) -> None:
    async def _task():
        from app.models.faq_entry import FaqEntry
        from app.services.bot_indexer import index_faq_entry
        async with AsyncSessionLocal() as s:
            entry = await s.get(FaqEntry, entry_id)
            if entry:
                await index_faq_entry(s, entry)
                await s.commit()
    asyncio.create_task(_task())

router = APIRouter(prefix="/admin/faq", tags=["admin: faq"])


# --- Категории ---

@router.post("/categories", response_model=FaqCategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: FaqCategoryCreateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqCategoryService(session).create(payload)


@router.get("/categories", response_model=list[FaqCategoryOut])
async def list_categories(
    search: str | None = Query(None, min_length=1, max_length=200),
    parent_id: int | None = Query(None, gt=0),
    sort_by: str = Query("sort_order", pattern="^(sort_order|name)$"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqCategoryService(session).list_all(search, parent_id, sort_by, sort_order)


@router.patch("/categories/{cat_id}", response_model=FaqCategoryOut)
async def update_category(
    cat_id: int,
    payload: FaqCategoryUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqCategoryService(session).update(cat_id, payload)


@router.delete("/categories/{cat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    cat_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await FaqCategoryService(session).delete(cat_id)


# --- Вопросы ---

@router.post("/entries", response_model=FaqEntryOut, status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: FaqEntryCreateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    entry = await FaqEntryService(session).create(payload)
    _reindex_faq(entry.id)
    return entry


@router.get("/entries", response_model=PageOut[FaqEntryOut])
async def list_entries(
    category_id: int | None = Query(None, gt=0),
    is_published: bool | None = Query(None),
    search: str | None = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("created_at", pattern="^(created_at|updated_at|question|version|is_published)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqEntryService(session).list_entries(
        category_id, search, sort_by, sort_order, page, size, is_published=is_published
    )


@router.patch("/entries/{entry_id}", response_model=FaqEntryOut)
async def update_entry(
    entry_id: int,
    payload: FaqEntryUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    entry = await FaqEntryService(session).update(entry_id, payload)
    _reindex_faq(entry.id)
    return entry


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await FaqEntryService(session).delete(entry_id)
