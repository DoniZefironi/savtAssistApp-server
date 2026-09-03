"""projects.bitrix_deal_id — сопоставление проекта со сделкой Bitrix по ID
сделки, а не по production_number.

Раньше production_number был unique, и upsert_project_from_deal искал
существующий проект только по нему — если в Bitrix заводили две разные
сделки с одинаковым номером (опечатка/дубль), вторая сделка тихо
обновляла проект первой вместо создания своего. Теперь основной ключ
идемпотентности — bitrix_deal_id (уникален), а production_number
используется только для одноразового бэкфилла старых проектов, у
которых bitrix_deal_id ещё не проставлен (см. upsert_project_from_deal).

Revision ID: b8f4a2e6c391
Revises: d4f9e6b2a731
Create Date: 2026-09-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8f4a2e6c391"
down_revision: Union[str, None] = "d4f9e6b2a731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("bitrix_deal_id", sa.String(20), nullable=True))
    op.create_index(
        op.f("ix_projects_bitrix_deal_id"), "projects", ["bitrix_deal_id"], unique=True
    )
    op.drop_index(op.f("ix_projects_production_number"), table_name="projects")
    op.create_index(
        op.f("ix_projects_production_number"), "projects", ["production_number"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_projects_production_number"), table_name="projects")
    op.create_index(
        op.f("ix_projects_production_number"), "projects", ["production_number"], unique=True
    )
    op.drop_index(op.f("ix_projects_bitrix_deal_id"), table_name="projects")
    op.drop_column("projects", "bitrix_deal_id")
