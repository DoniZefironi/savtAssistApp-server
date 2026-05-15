from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_photo import CabinetPhoto
from app.models.document import Document
from app.models.document_access import DocumentAccess
from app.models.document_request import DocumentRequest
from app.models.document_tag import DocumentTag
from app.models.user import User

_SORT_COLUMNS = {
    "title": Document.title,
    "doc_type": Document.doc_type,
    "file_size_bytes": Document.file_size_bytes,
    "created_at": Document.created_at,
}


def _sort_column(sort_by: str):
    return _SORT_COLUMNS.get(sort_by, Document.created_at)


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> Document:
        doc = Document(**kwargs)
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def get_by_id(self, doc_id: int) -> Document | None:
        return await self.session.get(Document, doc_id)

    async def delete(self, doc: Document) -> None:
        await self.session.delete(doc)
        await self.session.flush()

    async def list_admin(
        self,
        cabinet_id: int | None = None,
        doc_type: str | None = None,
        requires_approval: bool | None = None,
        tag_ids: list[int] | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Document], int]:
        conditions = []
        if cabinet_id is not None:
            conditions.append(Document.cabinet_id == cabinet_id)
        if doc_type:
            conditions.append(Document.doc_type == doc_type)
        if requires_approval is not None:
            conditions.append(Document.requires_approval == requires_approval)
        if tag_ids:
            tag_subq = (
                select(DocumentTag.document_id)
                .where(DocumentTag.tag_id.in_(tag_ids))
                .distinct()
                .scalar_subquery()
            )
            conditions.append(Document.id.in_(tag_subq))

        count_stmt = select(func.count(Document.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        sort_col = _sort_column(sort_by)
        order = sort_col.asc() if sort_order == "asc" else sort_col.desc()
        stmt = select(Document).order_by(order)
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def list_for_user(
        self,
        user_id: int,
        cabinet_id: int | None = None,
        tag_ids: list[int] | None = None,
        doc_type: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[tuple], int]:
        scope = or_(
            Document.cabinet_id == cabinet_id,
            Document.cabinet_id.is_(None),
        )
        access_subq = (
            select(DocumentAccess.document_id)
            .where(DocumentAccess.user_id == user_id)
            .scalar_subquery()
        )
        visibility = or_(
            Document.requires_approval == False,
            Document.id.in_(access_subq),
        )

        conditions = [scope, visibility]
        if tag_ids:
            tag_subq = (
                select(DocumentTag.document_id)
                .where(DocumentTag.tag_id.in_(tag_ids))
                .distinct()
                .scalar_subquery()
            )
            conditions.append(Document.id.in_(tag_subq))
        if doc_type:
            conditions.append(Document.doc_type == doc_type)

        count_stmt = select(func.count(Document.id)).where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        sort_col = _sort_column(sort_by)
        order = sort_col.asc() if sort_order == "asc" else sort_col.desc()

        stmt = (
            select(Document, DocumentAccess.document_id.label("access_doc_id"))
            .outerjoin(
                DocumentAccess,
                (DocumentAccess.document_id == Document.id) & (DocumentAccess.user_id == user_id),
            )
            .where(*conditions)
            .order_by(order)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all(), total

    async def has_access(self, user_id: int, doc_id: int) -> bool:
        result = await self.session.execute(
            select(DocumentAccess).where(
                DocumentAccess.user_id == user_id,
                DocumentAccess.document_id == doc_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def grant_access(self, user_id: int, doc_id: int, admin_id: int) -> None:
        existing = await self.has_access(user_id, doc_id)
        if not existing:
            self.session.add(DocumentAccess(
                user_id=user_id,
                document_id=doc_id,
                granted_by_admin_id=admin_id,
            ))
            await self.session.flush()


class PhotoRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> CabinetPhoto:
        photo = CabinetPhoto(**kwargs)
        self.session.add(photo)
        await self.session.flush()
        return photo

    async def get_by_id(self, photo_id: int) -> CabinetPhoto | None:
        return await self.session.get(CabinetPhoto, photo_id)

    async def delete(self, photo: CabinetPhoto) -> None:
        await self.session.delete(photo)
        await self.session.flush()

    async def list_all(
        self,
        cabinet_id: int,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[CabinetPhoto], int]:
        conditions = [CabinetPhoto.cabinet_id == cabinet_id]

        count_stmt = select(func.count(CabinetPhoto.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = select(CabinetPhoto).order_by(CabinetPhoto.sort_order, CabinetPhoto.created_at)
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return list(result.scalars().all()), total


class DocumentRequestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> DocumentRequest:
        req = DocumentRequest(**kwargs)
        self.session.add(req)
        await self.session.flush()
        return req

    async def get_by_id(self, request_id: int) -> DocumentRequest | None:
        return await self.session.get(DocumentRequest, request_id)

    async def find_pending(self, user_id: int, document_id: int) -> DocumentRequest | None:
        result = await self.session.execute(
            select(DocumentRequest).where(
                DocumentRequest.user_id == user_id,
                DocumentRequest.document_id == document_id,
                DocumentRequest.status == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def list_admin(
        self, status: str | None = None, offset: int = 0, limit: int = 20
    ) -> tuple[list, int]:
        count_stmt = select(func.count(DocumentRequest.id))
        if status:
            count_stmt = count_stmt.where(DocumentRequest.status == status)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(DocumentRequest, User)
            .join(User, User.id == DocumentRequest.user_id)
            .order_by(DocumentRequest.created_at.desc())
        )
        if status:
            stmt = stmt.where(DocumentRequest.status == status)
        result = await self.session.execute(stmt.offset(offset).limit(limit))
        return result.all(), total
