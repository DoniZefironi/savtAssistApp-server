import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.database import AsyncSessionLocal
from app.models.user import User
from app.schemas.documents import (
    ApproveDocumentRequestIn,
    DocumentOut,
    DocumentRequestOut,
    PhotoOut,
    PhotoUpdateIn,
    RejectDocumentRequestIn,
)
from app.schemas.pagination import PageOut
from app.services.document_service import AdminDocumentService

router = APIRouter(tags=["admin: documents"])


def _reindex_document(doc_id: int) -> None:
    async def _task():
        from app.models.document import Document
        from app.services.bot_indexer import index_document
        async with AsyncSessionLocal() as s:
            doc = await s.get(Document, doc_id)
            if doc:
                await index_document(s, doc)
                await s.commit()
    asyncio.create_task(_task())

# Загрузить документ
@router.post("/admin/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    file: UploadFile = File(...),
    cabinet_id: str = Form(...),
    title: str | None = Form(None),
    requires_approval: bool = Form(False),
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    if not cabinet_id or not cabinet_id.strip().isdigit():
        raise HTTPException(status_code=422, detail="cabinet_id обязателен и должен быть числом")
    doc = await AdminDocumentService(session).create_document(
        file=file,
        cabinet_id=int(cabinet_id),
        title=title.strip() or None if title else None,
        requires_approval=requires_approval,
        actor_id=actor.id,
        actor_role=actor_role,
    )
    _reindex_document(doc.id)
    return doc

# Все документы
@router.get("/admin/documents", response_model=PageOut[DocumentOut])
async def list_documents(
    cabinet_id: int | None = Query(None),
    doc_type: str | None = Query(None),
    requires_approval: bool | None = Query(None),
    tag_ids: list[int] = Query(default=[]),
    sort_by: str = Query("created_at", pattern="^(title|doc_type|file_size_bytes|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminDocumentService(session).list_documents(
        cabinet_id=cabinet_id, doc_type=doc_type,
        requires_approval=requires_approval,
        tag_ids=tag_ids or None,
        sort_by=sort_by, sort_order=sort_order,
        page=page, size=size,
    )

# Удалить документ
@router.delete("/admin/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminDocumentService(session).delete_document(doc_id, actor.id, actor_role)

# Загрузить фото
@router.post("/admin/photos", response_model=PhotoOut, status_code=status.HTTP_201_CREATED)
async def create_photo(
    file: UploadFile = File(...),
    cabinet_id: str = Form(...),
    caption: str | None = Form(None),
    sort_order: str = Form("0"),
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    if not cabinet_id or not cabinet_id.strip().isdigit():
        raise HTTPException(status_code=422, detail="cabinet_id обязателен и должен быть числом")
    return await AdminDocumentService(session).create_photo(
        file=file,
        cabinet_id=int(cabinet_id),
        caption=caption.strip() or None if caption else None,
        sort_order=int(sort_order) if sort_order and sort_order.strip().lstrip("-").isdigit() else 0,
    )

# Все фото
@router.get("/admin/photos", response_model=PageOut[PhotoOut])
async def list_photos(
    cabinet_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminDocumentService(session).list_photos(cabinet_id=cabinet_id, page=page, size=size)

# Изменить подпись
@router.patch("/admin/photos/{photo_id}", response_model=PhotoOut)
async def update_photo(
    photo_id: int,
    payload: PhotoUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminDocumentService(session).update_photo(photo_id, payload)

# Удалить фото
@router.delete("/admin/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo(
    photo_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await AdminDocumentService(session).delete_photo(photo_id)

# Заявки на доступ
@router.get("/admin/document-requests", response_model=PageOut[DocumentRequestOut])
async def list_document_requests(
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    search: str | None = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("created_at", pattern="^(created_at|status|user_full_name|doc_type)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminDocumentService(session).list_requests(
        status=status, page=page, size=size, search=search, sort_by=sort_by, sort_order=sort_order
    )

# Одобрить
@router.post("/admin/document-requests/{request_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_document_request(
    request_id: int,
    payload: ApproveDocumentRequestIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminDocumentService(session).approve_request(request_id, payload, actor.id, actor_role)

# Отклонить
@router.post("/admin/document-requests/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_document_request(
    request_id: int,
    payload: RejectDocumentRequestIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminDocumentService(session).reject_request(request_id, payload, actor.id, actor_role)
