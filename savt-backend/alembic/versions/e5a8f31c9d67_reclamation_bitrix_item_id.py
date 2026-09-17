"""reclamations.bitrix_item_id — ID созданного элемента в смарт-процессе
Bitrix24 ("Журнал рекламаций и претензий", entityTypeId=1176). См.
app/models/reclamation.py.

Revision ID: e5a8f31c9d67
Revises: d4f8b2e91a56
Create Date: 2026-09-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5a8f31c9d67"
down_revision: Union[str, None] = "d4f8b2e91a56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reclamations",
        sa.Column("bitrix_item_id", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reclamations", "bitrix_item_id")
