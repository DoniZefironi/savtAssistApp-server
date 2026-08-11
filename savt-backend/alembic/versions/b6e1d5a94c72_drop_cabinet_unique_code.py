"""убрать unique_code (кур-код) у ШУ — QR остаётся только у проекта

Revision ID: b6e1d5a94c72
Revises: a29e6c4d8f13
Create Date: 2026-08-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6e1d5a94c72"
down_revision: Union[str, None] = "a29e6c4d8f13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_cabinets_unique_code"), table_name="cabinets")
    op.drop_column("cabinets", "unique_code")


def downgrade() -> None:
    op.add_column("cabinets", sa.Column("unique_code", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_cabinets_unique_code"), "cabinets", ["unique_code"], unique=True)
