from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
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

router = APIRouter(prefix="/admin/faq", tags=["admin: faq"])


# --- Категории ---

@router.post("/categories", response_model=FaqCategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: FaqCategoryCreateIn,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqCategoryService(session).create(payload)


@router.get("/categories", response_model=list[FaqCategoryOut])
async def list_categories(
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqCategoryService(session).list_all()


@router.patch("/categories/{cat_id}", response_model=FaqCategoryOut)
async def update_category(
    cat_id: int,
    payload: FaqCategoryUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
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
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqEntryService(session).create(payload)


@router.get("/entries", response_model=PageOut[FaqEntryOut])
async def list_entries(
    category_id: int | None = Query(None, gt=0),
    search: str | None = Query(None, min_length=1, max_length=200),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqEntryService(session).list_entries(category_id, search, page, size)


@router.patch("/entries/{entry_id}", response_model=FaqEntryOut)
async def update_entry(
    entry_id: int,
    payload: FaqEntryUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await FaqEntryService(session).update(entry_id, payload)


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await FaqEntryService(session).delete(entry_id)
