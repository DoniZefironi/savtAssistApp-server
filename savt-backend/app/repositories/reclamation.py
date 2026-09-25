from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinets import Cabinet
from app.models.project import Project
from app.models.reclamation import Reclamation
from app.models.reclamation_attachment import ReclamationAttachment
from app.models.user import User


class ReclamationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: int, data: dict) -> Reclamation:
        rec = Reclamation(user_id=user_id, **data)
        self.session.add(rec)
        await self.session.flush()
        return rec

    async def add_attachment(self, reclamation_id: int, data: dict) -> ReclamationAttachment:
        att = ReclamationAttachment(reclamation_id=reclamation_id, **data)
        self.session.add(att)
        await self.session.flush()
        return att

    async def get_by_id(self, reclamation_id: int) -> Reclamation | None:
        return await self.session.get(Reclamation, reclamation_id)

    async def find_by_bitrix_item_id(self, bitrix_item_id: str) -> Reclamation | None:
        result = await self.session.execute(
            select(Reclamation).where(Reclamation.bitrix_item_id == bitrix_item_id)
        )
        return result.scalar_one_or_none()

    async def list_bitrix_detached(self):
        """Рекламации, чью карточку удалили в Bitrix (см.
        reclamation_service.mark_bitrix_item_deleted). Свежие сверху —
        разбираться начинают с последних."""
        from app.models.user import User

        result = await self.session.execute(
            select(Reclamation, User)
            .join(User, User.id == Reclamation.user_id)
            .where(Reclamation.bitrix_deleted_at.is_not(None))
            .order_by(Reclamation.bitrix_deleted_at.desc())
        )
        return result.all()

    # для пользователя — с проверкой владения прямо в условии, а не отдельным
    # if user_id != ... после загрузки (чужая заявка просто не найдётся).
    # Cabinet и Project оба outerjoin — у рекламации заполнено ровно одно
    # (см. CHECK ck_reclamation_cabinet_or_project), второй столбец просто NULL
    async def get_with_cabinet_for_user(self, user_id: int, reclamation_id: int):
        result = await self.session.execute(
            select(Reclamation, Cabinet, Project)
            .outerjoin(Cabinet, Cabinet.id == Reclamation.cabinet_id)
            .outerjoin(Project, Project.id == Reclamation.project_id)
            .where(Reclamation.id == reclamation_id, Reclamation.user_id == user_id)
        )
        return result.one_or_none()

    async def list_for_user(
        self, user_id: int, status: str | None = None, offset: int = 0, limit: int = 20,
    ) -> tuple[list[tuple], int]:
        conditions = [Reclamation.user_id == user_id]
        if status:
            conditions.append(Reclamation.status == status)

        total = (await self.session.execute(
            select(func.count(Reclamation.id)).where(*conditions)
        )).scalar() or 0

        stmt = (
            select(Reclamation, Cabinet, Project)
            .outerjoin(Cabinet, Cabinet.id == Reclamation.cabinet_id)
            .outerjoin(Project, Project.id == Reclamation.project_id)
            .where(*conditions)
            .order_by(Reclamation.created_at.desc())
            .offset(offset).limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all(), total

    # админка — с автором и ШУ разом, кто сейчас единолично обрабатывает
    # заявки (см. Reclamation.__doc__), поэтому владение не проверяется
    async def get_with_relations(self, reclamation_id: int):
        result = await self.session.execute(
            select(Reclamation, User, Cabinet, Project)
            .join(User, User.id == Reclamation.user_id)
            .outerjoin(Cabinet, Cabinet.id == Reclamation.cabinet_id)
            .outerjoin(Project, Project.id == Reclamation.project_id)
            .where(Reclamation.id == reclamation_id)
        )
        return result.one_or_none()

    async def list_admin(
        self,
        status: str | None = None,
        object_type: str | None = None,
        warranty_classification: bool | None = None,
        offset: int = 0, limit: int = 20,
    ) -> tuple[list[tuple], int]:
        conditions = []
        if status:
            conditions.append(Reclamation.status == status)
        if object_type:
            conditions.append(Reclamation.object_type == object_type)
        if warranty_classification is not None:
            conditions.append(Reclamation.warranty_classification == warranty_classification)

        count_stmt = select(func.count(Reclamation.id)).join(User, User.id == Reclamation.user_id)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(Reclamation, User, Cabinet, Project)
            .join(User, User.id == Reclamation.user_id)
            .outerjoin(Cabinet, Cabinet.id == Reclamation.cabinet_id)
            .outerjoin(Project, Project.id == Reclamation.project_id)
        )
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(Reclamation.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return result.all(), total

    async def list_attachments(self, reclamation_id: int) -> list[ReclamationAttachment]:
        result = await self.session.execute(
            select(ReclamationAttachment)
            .where(ReclamationAttachment.reclamation_id == reclamation_id)
            .order_by(ReclamationAttachment.created_at)
        )
        return list(result.scalars().all())
