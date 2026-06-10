from datetime import datetime, timezone

from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.repositories.document import DocumentRepository, DocumentRequestRepository, PhotoRepository
from app.repositories.tag import TagRepository
from app.services.audit_service import AuditLogger
from app.services.upload_service import UPLOAD_ROOT
from app.schemas.documents import (
    ApproveDocumentRequestIn,
    DocumentOut,
    DocumentRequestOut,
    PhotoOut,
    PhotoUpdateIn,
    RejectDocumentRequestIn,
    UserDocumentOut,
)
from app.schemas.pagination import PageOut, make_page
from app.services.upload_service import save_attachment_with_meta


class AdminDocumentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.doc_repo = DocumentRepository(session)
        self.photo_repo = PhotoRepository(session)
        self.request_repo = DocumentRequestRepository(session)
        self.tag_repo = TagRepository(session)
        self.audit = AuditLogger(session)

    async def create_document(
        self,
        file: UploadFile,
        cabinet_id: int | None,
        title: str | None,
        requires_approval: bool,
        actor_id: int = 0,
        actor_role: str = "admin",
    ) -> DocumentOut:
        info = await save_attachment_with_meta(file)
        doc = await self.doc_repo.create(
            cabinet_id=cabinet_id,
            title=title or (file.filename or "Документ"),
            doc_type=info.doc_type,
            file_url=info.url,
            file_size_bytes=info.file_size_bytes,
            mime_type=info.mime_type,
            requires_approval=requires_approval,
        )
        await self.session.flush()
        self.audit.log("document.create", "document", doc.id, actor_id, actor_role,
                       {"title": doc.title, "cabinet_id": cabinet_id})
        await self.session.commit()
        await self.session.refresh(doc)
        return DocumentOut.model_validate(doc)

    async def list_documents(
        self,
        cabinet_id: int | None = None,
        doc_type: str | None = None,
        requires_approval: bool | None = None,
        tag_ids: list[int] | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[DocumentOut]:
        items, total = await self.doc_repo.list_admin(
            cabinet_id=cabinet_id,
            doc_type=doc_type,
            requires_approval=requires_approval,
            tag_ids=tag_ids,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=(page - 1) * size,
            limit=size,
        )
        tags_map = await self.tag_repo.get_tags_for_documents([d.id for d in items])
        out = []
        for d in items:
            doc_out = DocumentOut.model_validate(d)
            doc_out.tags = [t for t in tags_map.get(d.id, [])]
            out.append(doc_out)
        return make_page(out, total, page, size)

    async def delete_document(self, doc_id: int, actor_id: int = 0, actor_role: str = "admin") -> None:
        doc = await self.doc_repo.get_by_id(doc_id)
        if doc is None:
            raise NotFoundError("Документ не найден")
        self.audit.log("document.delete", "document", doc_id, actor_id, actor_role, {"title": doc.title})
        await self.doc_repo.delete(doc)
        await self.session.commit()

    async def create_photo(
        self,
        file: UploadFile,
        cabinet_id: int | None,
        caption: str | None,
        sort_order: int,
    ) -> PhotoOut:
        info = await save_attachment_with_meta(file)
        photo = await self.photo_repo.create(
            cabinet_id=cabinet_id,
            url=info.url,
            caption=caption,
            sort_order=sort_order,
        )
        await self.session.commit()
        await self.session.refresh(photo)
        return PhotoOut.model_validate(photo)

    async def list_photos(
        self, cabinet_id: int | None, page: int = 1, size: int = 50
    ) -> PageOut[PhotoOut]:
        items, total = await self.photo_repo.list_all(
            cabinet_id=cabinet_id, offset=(page - 1) * size, limit=size
        )
        return make_page([PhotoOut.model_validate(p) for p in items], total, page, size)

    async def update_photo(self, photo_id: int, data: PhotoUpdateIn) -> PhotoOut:
        photo = await self.photo_repo.get_by_id(photo_id)
        if photo is None:
            raise NotFoundError("Фото не найдено")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(photo, field, value)
        await self.session.commit()
        await self.session.refresh(photo)
        return PhotoOut.model_validate(photo)

    async def delete_photo(self, photo_id: int) -> None:
        photo = await self.photo_repo.get_by_id(photo_id)
        if photo is None:
            raise NotFoundError("Фото не найдено")
        await self.photo_repo.delete(photo)
        await self.session.commit()

    async def list_requests(
        self, status: str | None = None, page: int = 1, size: int = 20,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> PageOut[DocumentRequestOut]:
        rows, total = await self.request_repo.list_admin(
            status=status, search=search, sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size
        )
        items = [
            DocumentRequestOut(
                id=req.id,
                user_id=req.user_id,
                user_full_name=user.full_name,
                document_id=req.document_id,
                cabinet_id=req.cabinet_id,
                doc_type=req.doc_type,
                status=req.status,
                user_message=req.user_message,
                admin_response=req.admin_response,
                created_at=req.created_at,
                resolved_at=req.resolved_at,
            )
            for req, user in rows
        ]
        return make_page(items, total, page, size)

    async def approve_request(
        self, request_id: int, data: ApproveDocumentRequestIn, admin_id: int, actor_role: str = "admin"
    ) -> None:
        req = await self.request_repo.get_by_id(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")
        if req.document_id is None:
            raise AlreadyExistsError("Сначала укажите документ в заявке")
        await self.doc_repo.grant_access(req.user_id, req.document_id, admin_id)
        req.status = "approved"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)
        self.audit.log("document_request.approve", "document_request", request_id, admin_id, actor_role,
                       {"user_id": req.user_id, "document_id": req.document_id})
        await self.session.commit()

    async def reject_request(
        self, request_id: int, data: RejectDocumentRequestIn, admin_id: int, actor_role: str = "admin"
    ) -> None:
        req = await self.request_repo.get_by_id(request_id)
        if req is None:
            raise NotFoundError("Заявка не найдена")
        if req.status != "pending":
            raise AlreadyExistsError("Заявка уже обработана")
        req.status = "rejected"
        req.admin_response = data.admin_response
        req.resolved_by_admin_id = admin_id
        req.resolved_at = datetime.now(timezone.utc)
        self.audit.log("document_request.reject", "document_request", request_id, admin_id, actor_role,
                       {"user_id": req.user_id, "reason": data.admin_response})
        await self.session.commit()


class UserDocumentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.doc_repo = DocumentRepository(session)
        self.photo_repo = PhotoRepository(session)
        self.request_repo = DocumentRequestRepository(session)
        self.tag_repo = TagRepository(session)

    async def list_documents(
        self,
        user_id: int,
        cabinet_id: int | None = None,
        tag_ids: list[int] | None = None,
        doc_type: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[UserDocumentOut]:
        rows, total = await self.doc_repo.list_for_user(
            user_id=user_id, cabinet_id=cabinet_id,
            tag_ids=tag_ids, doc_type=doc_type,
            sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size,
        )
        doc_ids = [doc.id for doc, _ in rows]
        tags_map = await self.tag_repo.get_tags_for_documents(doc_ids)
        items = []
        for doc, access_doc_id in rows:
            has_access = not doc.requires_approval or access_doc_id is not None
            items.append(UserDocumentOut(
                id=doc.id,
                cabinet_id=doc.cabinet_id,
                title=doc.title,
                doc_type=doc.doc_type,
                file_url=doc.file_url if has_access else None,
                file_size_bytes=doc.file_size_bytes,
                mime_type=doc.mime_type,
                has_access=has_access,
                tags=tags_map.get(doc.id, []),
            ))
        return make_page(items, total, page, size)

    async def get_file_path(self, user_id: int, doc_id: int) -> tuple[Path, str, str]:
        doc = await self.doc_repo.get_by_id(doc_id)
        if doc is None:
            raise NotFoundError("Документ не найден")
        if doc.requires_approval and not await self.doc_repo.has_access(user_id, doc_id):
            raise PermissionDeniedError("Нет доступа к этому документу")
        file_path = UPLOAD_ROOT / doc.file_url.removeprefix("/static/")
        return file_path, doc.mime_type, doc.title

    async def list_photos(
        self, cabinet_id: int, page: int = 1, size: int = 50
    ) -> PageOut[PhotoOut]:
        items, total = await self.photo_repo.list_all(
            cabinet_id=cabinet_id, offset=(page - 1) * size, limit=size
        )
        return make_page([PhotoOut.model_validate(p) for p in items], total, page, size)

    async def request_access(
        self, user_id: int, doc_id: int, user_message: str | None
    ) -> int:
        doc = await self.doc_repo.get_by_id(doc_id)
        if doc is None:
            raise NotFoundError("Документ не найден")
        if not doc.requires_approval:
            raise AlreadyExistsError("Документ доступен без запроса")
        if await self.doc_repo.has_access(user_id, doc_id):
            raise AlreadyExistsError("Доступ уже есть")
        pending = await self.request_repo.find_pending(user_id, doc_id)
        if pending is not None:
            raise AlreadyExistsError("Заявка уже отправлена")

        req = await self.request_repo.create(
            user_id=user_id,
            document_id=doc_id,
            cabinet_id=doc.cabinet_id,
            doc_type=doc.doc_type,
            user_message=user_message,
        )
        await self.session.commit()
        return req.id
