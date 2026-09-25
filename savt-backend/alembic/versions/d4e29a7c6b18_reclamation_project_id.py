"""reclamations.project_id — прямая привязка к проекту для всех типов
объекта, не только "cabinet". Нужна потому, что 2026-09-25 поле "Клиент"
(компания-заказчик) в Bitrix стало обязательным при создании элемента —
без deal_id/company_id из проекта crm.item.add падает
CRM_FIELD_ERROR_REQUIRED. У рекламаций типа line/component/software/
documentation компанию раньше было взять неоткуда: cabinet_id для них не
заполняется, а через него единственно и вычислялся проект.

Паттерн — как у ServiceRequest: ровно одно из cabinet_id/project_id.
CHECK добавляется NOT VALID: на проде уже есть рекламации, где project_id
ещё не существовал и не может быть заполнен задним числом (у части
object_type там нет данных, откуда его взять) — валидировать их незачем,
достаточно чтобы новые/изменяемые строки соблюдали правило.

Revision ID: d4e29a7c6b18
Revises: c5a71e93b8d4
Create Date: 2026-09-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4e29a7c6b18"
down_revision: Union[str, None] = "c5a71e93b8d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reclamations",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
    )
    op.create_index(
        "ix_reclamations_project_id", "reclamations", ["project_id"],
    )
    op.execute(
        "ALTER TABLE reclamations ADD CONSTRAINT ck_reclamation_cabinet_or_project "
        "CHECK ((cabinet_id IS NOT NULL) != (project_id IS NOT NULL)) NOT VALID"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE reclamations DROP CONSTRAINT ck_reclamation_cabinet_or_project")
    op.drop_index("ix_reclamations_project_id", table_name="reclamations")
    op.drop_column("reclamations", "project_id")
