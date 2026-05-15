from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.documents import (
    DocumentRequestCreateIn,
    PhotoOut,
    UserDocumentOut,
)
from app.schemas.pagination import PageOut
from app.services.document_service import UserDocumentService

router = APIRouter(tags=["documents"])


_SORT_PATTERN = "^(title|doc_type|file_size_bytes|created_at)$"


@router.get("/cabinets/{cabinet_id}/documents", response_model=PageOut[UserDocumentOut])
async def list_cabinet_documents(
    cabinet_id: int,
    tag_ids: list[int] = Query(default=[]),
    doc_type: str | None = Query(None),
    sort_by: str = Query("created_at", pattern=_SORT_PATTERN),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await UserDocumentService(session).list_documents(
        user_id=current_user.id, cabinet_id=cabinet_id,
        tag_ids=tag_ids or None, doc_type=doc_type,
        sort_by=sort_by, sort_order=sort_order,
        page=page, size=size,
    )


@router.get("/documents/{doc_id}/download")
async def download_document(
    doc_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    file_path, mime_type, title = await UserDocumentService(session).get_file_path(
        user_id=current_user.id, doc_id=doc_id
    )
    return FileResponse(path=str(file_path), media_type=mime_type, filename=title)


@router.get("/cabinets/{cabinet_id}/photos", response_model=PageOut[PhotoOut])
async def list_cabinet_photos(
    cabinet_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await UserDocumentService(session).list_photos(
        cabinet_id=cabinet_id, page=page, size=size
    )


@router.post("/documents/{doc_id}/request-access", status_code=status.HTTP_201_CREATED)
async def request_document_access(
    doc_id: int,
    payload: DocumentRequestCreateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    request_id = await UserDocumentService(session).request_access(
        user_id=current_user.id, doc_id=doc_id, user_message=payload.user_message
    )
    return {"request_id": request_id, "message": "Заявка отправлена"}
