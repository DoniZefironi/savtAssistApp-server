from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinets import Cabinet
from app.models.project import Project
from app.models.reclamation import Reclamation
from app.models.reclamation_attachment import ReclamationAttachment
from app.models.user import User
from app.utils.db import date_condition, fuzzy_condition, words_condition
from app.utils.search_labels import (
    RECLAMATION_OBJECT_TYPE, RECLAMATION_STATUS, label_condition, warranty_label_condition,
)


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
        search: str | None = None,
        sort_by: str = "created_at", sort_order: str = "desc",
        offset: int = 0, limit: int = 20,
    ) -> tuple[list[tuple], int]:
        conditions = []
        if status:
            conditions.append(Reclamation.status == status)
        if object_type:
            conditions.append(Reclamation.object_type == object_type)
        if warranty_classification is not None:
            conditions.append(Reclamation.warranty_classification == warranty_classification)
        if search:
            # Заводской номер лежит в object_details (JSONB), у cabinet/line/
            # component под одним ключом serial_number. Проект и ШУ — только у
            # рекламаций, поданных со связью с ними (у новых обоих нет)
            # Слова запроса ("ШУ 26") разбираются по отдельности: "ШУ" — подпись
            # типа, "26" — часть номера ШУ, вместе в одном поле их нет
            def match_word(word: str):
                by_text = fuzzy_condition(
                    word,
                    User.full_name, User.phone,
                    Reclamation.contact_name, Reclamation.contact_phone,
                    Reclamation.description, Reclamation.error_codes,
                    Reclamation.contract_number, Reclamation.order_number, Reclamation.ttn_number,
                    Reclamation.object_details["serial_number"].astext,
                    Project.name, Cabinet.object_number,
                )
                extra = (
                    label_condition(word, Reclamation.object_type, RECLAMATION_OBJECT_TYPE),
                    label_condition(word, Reclamation.status, RECLAMATION_STATUS),
                    warranty_label_condition(word, Reclamation.warranty_classification),
                    date_condition(
                        word,
                        (Reclamation.created_at, True), (Reclamation.resolved_at, True),
                        (Reclamation.deadline_at, False),
                    ),
                )
                return or_(by_text, *[c for c in extra if c is not None])

            conditions.append(words_condition(search, match_word))

        count_stmt = (
            select(func.count(Reclamation.id))
            .join(User, User.id == Reclamation.user_id)
            .outerjoin(Cabinet, Cabinet.id == Reclamation.cabinet_id)
            .outerjoin(Project, Project.id == Reclamation.project_id)
        )
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        _sort_col = {
            "created_at": Reclamation.created_at,
            "resolved_at": Reclamation.resolved_at,
            "status": Reclamation.status,
            "deadline_at": Reclamation.deadline_at,
            "object_type": Reclamation.object_type,
            "user_full_name": User.full_name,
        }.get(sort_by, Reclamation.created_at)
        order = (_sort_col.asc() if sort_order == "asc" else _sort_col.desc()).nulls_last()

        stmt = (
            select(Reclamation, User, Cabinet, Project)
            .join(User, User.id == Reclamation.user_id)
            .outerjoin(Cabinet, Cabinet.id == Reclamation.cabinet_id)
            .outerjoin(Project, Project.id == Reclamation.project_id)
        )
        if conditions:
            stmt = stmt.where(*conditions)
        # id вторым ключом — иначе при равных значениях (статус, тип) порядок
        # между страницами не гарантирован, и записи могут повторяться/теряться
        stmt = stmt.order_by(order, Reclamation.id.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return result.all(), total

    async def list_attachments(self, reclamation_id: int) -> list[ReclamationAttachment]:
        result = await self.session.execute(
            select(ReclamationAttachment)
            .where(ReclamationAttachment.reclamation_id == reclamation_id)
            .order_by(ReclamationAttachment.created_at)
        )
        return list(result.scalars().all())
