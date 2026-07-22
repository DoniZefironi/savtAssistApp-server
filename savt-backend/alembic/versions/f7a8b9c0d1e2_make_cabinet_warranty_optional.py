"""make cabinet warranty dates optional

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("cabinets", "warranty_starts_at", nullable=True)
    op.alter_column("cabinets", "warranty_ends_at", nullable=True)


def downgrade() -> None:
    # Внимание: откат упадёт, если к этому моменту в БД уже есть ШУ без гарантии —
    # сначала нужно проставить им даты вручную.
    op.alter_column("cabinets", "warranty_starts_at", nullable=False)
    op.alter_column("cabinets", "warranty_ends_at", nullable=False)
