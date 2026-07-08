"""add cabinet soft delete

Revision ID: c9d1e0f8a695
Revises: b5c8d7e6f584
Create Date: 2026-07-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d1e0f8a695"
down_revision: Union[str, None] = "b5c8d7e6f584"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cabinets", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_cabinets_deleted_at"), "cabinets", ["deleted_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_cabinets_deleted_at"), table_name="cabinets")
    op.drop_column("cabinets", "deleted_at")
