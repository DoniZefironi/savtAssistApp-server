from datetime import datetime, timezone

from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.repositories.document import DocumentRepository, DocumentRequestRepository, PhotoRepository
from app.repositories.tag import TagRepository
from app.services import project_folder_service
from app.services.audit_service import AuditLogger
from app.services.upload_service import UPLOAD_ROOT
from app.schemas.documents import (
    ApproveDocumentRequestIn,
    DocumentOut,
    DocumentRequestOut,
    DocumentUpdateIn,
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

    # Заявитель узнаёт о решении, а не выясняет его, заходя в приложение
    async def _notify(self, user_id: int, title: str, body: str | None, data: dict) -> None:
        from app.services.notification_service import NotificationService
        await NotificationService(self.session).send(
            user_id=user_id, type_="request_status",
            title=title, body=body or "Решение принято администратором", data=data,
        )

    async def create_document(
        self,
        file: UploadFile,
        cabinet_id: int | None,
        project_id: int | None,
        title: str | None,
        requires_approval: bool,
        is_internal: bool = False,
        actor_id: int = 0,
        actor_role: str = "admin",
    ) -> DocumentOut:
        info = await save_attachment_with_meta(file)
        doc = await self.doc_repo.create(
            cabinet_id=cabinet_id,
            project_id=project_id,
            title=title or (file.filename or "Документ"),
            doc_type=info.doc_type,
            file_url=info.url,
            file_size_bytes=info.file_size_bytes,
            mime_type=info.mime_type,
            requires_approval=requires_approval,
            is_internal=is_internal,
        )
        await self.session.flush()
        self.audit.log("document.create", "document", doc.id, actor_id, actor_role,
                       {"title": doc.title, "cabinet_id": cabinet_id, "project_id": project_id})
        await self.session.commit()
        await self.session.refresh(doc)
        project_folder_service.schedule_document_mirror(doc.id)
        return DocumentOut.model_validate(doc)

    # Единственный способ изменить requires_approval/is_internal после создания —
    # нужно, в частности, чтобы разобрать документы, автоматически подхваченные
    # с NAS как is_internal=True (см. project_folder_service.import_new_files_from_nas):
    # без этого их некому было бы вручную открыть.
    async def update_document(
        self, doc_id: int, data: DocumentUpdateIn, actor_id: int = 0, actor_role: str = "admin",
    ) -> DocumentOut:
        doc = await self.doc_repo.get_by_id(doc_id)
        if doc is None:
            raise NotFoundError("Документ не найден")
        changed = data.model_dump(exclude_unset=True)
        for field, value in changed.items():
            setattr(doc, field, value)
        if changed:
            self.audit.log("document.update", "document", doc_id, actor_id, actor_role, {"fields": list(changed.keys())})
            await self.session.commit()
            await self.session.refresh(doc)
            if "is_internal" in changed:
                # Открыли — переиндексировать, чтобы стал находиться поиском бота.
                # Закрыли — переиндексировать тоже: index_document для is_internal
                # сам чистит уже существующие эмбеддинги (см. bot_indexer.py).
                from app.services.bot_indexer import schedule_reindex_document
                schedule_reindex_document(doc_id)
        return DocumentOut.model_validate(doc)

    async def list_documents(
        self,
        cabinet_id: int | None = None,
        project_id: int | None = None,
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
            project_id=project_id,
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
        from sqlalchemy import delete as sa_delete
        from app.models.embedding import Embedding
        doc = await self.doc_repo.get_by_id(doc_id)
        if doc is None:
            raise NotFoundError("Документ не найден")
        self.audit.log("document.delete", "document", doc_id, actor_id, actor_role, {"title": doc.title})
        await self.session.execute(
            sa_delete(Embedding).where(
                Embedding.source_type == "document",
                Embedding.source_id == doc_id,
            )
        )
        cabinet_id, project_id, title, file_url = doc.cabinet_id, doc.project_id, doc.title, doc.file_url
        await self.doc_repo.delete(doc)
        await self.session.commit()
        project_folder_service.schedule_document_removal(
            cabinet_id=cabinet_id, project_id=project_id, title=title, file_url=file_url,
        )

    async def create_photo(
        self,
        file: UploadFile,
        cabinet_id: int | None,
        caption: str | None,
        sort_order: int,
        project_id: int | None = None,
    ) -> PhotoOut:
        info = await save_attachment_with_meta(file)
        photo = await self.photo_repo.create(
            cabinet_id=cabinet_id,
            project_id=project_id,
            url=info.url,
            caption=caption,
            sort_order=sort_order,
        )
        await self.session.commit()
        await self.session.refresh(photo)
        return PhotoOut.model_validate(photo)

    async def list_photos(
        self, cabinet_id: int | None, page: int = 1, size: int = 50,
        project_id: int | None = None,
    ) -> PageOut[PhotoOut]:
        items, total = await self.photo_repo.list_all(
            cabinet_id=cabinet_id, project_id=project_id,
            offset=(page - 1) * size, limit=size,
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
        cabinet_id, project_id, nas_filename = photo.cabinet_id, photo.project_id, photo.nas_filename
        await self.photo_repo.delete(photo)
        await self.session.commit()
        project_folder_service.schedule_photo_removal(
            cabinet_id=cabinet_id, project_id=project_id, nas_filename=nas_filename,
        )

    async def list_requests(
        self, status: str | None = None, page: int = 1, size: int = 20,
        resolved_by_admin_id: int | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> PageOut[DocumentRequestOut]:
        rows, total = await self.request_repo.list_admin(
            status=status, resolved_by_admin_id=resolved_by_admin_id, search=search,
            sort_by=sort_by, sort_order=sort_order,
            offset=(page - 1) * size, limit=size
        )
        items = [
            DocumentRequestOut(
                id=req.id,
                user_id=req.user_id,
                user_full_name=user.full_name,
                user_phone=user.phone,
                user_type=user.user_type,
                organization_name=user.organization_name,
                user_is_verified=user.is_verified,
                user_registered_at=user.created_at,
                document_id=req.document_id,
                cabinet_id=req.cabinet_id,
                project_id=req.project_id,
                doc_type=req.doc_type,
                status=req.status,
                user_message=req.user_message,
                admin_response=req.admin_response,
                resolved_by_admin_id=req.resolved_by_admin_id,
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
        await self._notify(req.user_id, "Доступ к документу открыт",
                           data.admin_response or "Документ доступен для скачивания",
                           {"type": "document_request", "document_id": str(req.document_id)})

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
        await self._notify(req.user_id, "Заявка на доступ к документу отклонена",
                           data.admin_response, {"type": "document_request"})


class UserDocumentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.doc_repo = DocumentRepository(session)
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
                project_id=doc.project_id,
                title=doc.title,
                doc_type=doc.doc_type,
                # Ограниченные документы не отдают прямую ссылку никому, даже тем,
                # кому доступ одобрен: подписанный /static/-URL можно переслать
                # третьему лицу, а GET /documents/{id}/download проверяет права
                # на каждое скачивание. Свободные документы отдаются ссылкой.
                file_url=doc.file_url if not doc.requires_approval else None,
                file_size_bytes=doc.file_size_bytes,
                mime_type=doc.mime_type,
                has_access=has_access,
                tags=tags_map.get(doc.id, []),
            ))
        return make_page(items, total, page, size)

    # Документация проекта в целом — отдельный список от документов ШУ
    # (см. list_documents), даже если ШУ входит в этот проект
    async def list_project_documents(
        self,
        user_id: int,
        project_id: int,
        tag_ids: list[int] | None = None,
        doc_type: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[UserDocumentOut]:
        rows, total = await self.doc_repo.list_for_project(
            user_id=user_id, project_id=project_id,
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
                project_id=doc.project_id,
                title=doc.title,
                doc_type=doc.doc_type,
                # Ограниченные документы не отдают прямую ссылку никому, даже тем,
                # кому доступ одобрен: подписанный /static/-URL можно переслать
                # третьему лицу, а GET /documents/{id}/download проверяет права
                # на каждое скачивание. Свободные документы отдаются ссылкой.
                file_url=doc.file_url if not doc.requires_approval else None,
                file_size_bytes=doc.file_size_bytes,
                mime_type=doc.mime_type,
                has_access=has_access,
                tags=tags_map.get(doc.id, []),
            ))
        return make_page(items, total, page, size)

    async def get_file_path(self, user_id: int, doc_id: int) -> tuple[Path, str, str]:
        doc = await self.doc_repo.get_by_id(doc_id)
        # Служебный документ пользователю не показывается вовсе — отвечаем как на
        # несуществующий, чтобы по коду ответа нельзя было узнать, что он есть
        if doc is None or doc.is_internal:
            raise NotFoundError("Документ не найден")
        if doc.requires_approval and not await self.doc_repo.has_access(user_id, doc_id):
            raise PermissionDeniedError("Нет доступа к этому документу")
        file_path = UPLOAD_ROOT / doc.file_url.removeprefix("/static/")
        return file_path, doc.mime_type, doc.title

    # Фото пользователю не показываются вообще — см. AdminDocumentService.list_photos,
    # им пользуются только GET /cabinets/{id}/photos и GET /projects/{id}/photos
    # под require_role(ADMIN, OPERATOR) (см. routers/documents.py).

    async def request_access(
        self, user_id: int, doc_id: int, user_message: str | None
    ) -> int:
        doc = await self.doc_repo.get_by_id(doc_id)
        # Служебный документ пользователю не виден, запрашивать к нему доступ нельзя
        if doc is None or doc.is_internal:
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
            project_id=doc.project_id,
            doc_type=doc.doc_type,
            user_message=user_message,
        )
        await self.session.commit()
        return req.id
