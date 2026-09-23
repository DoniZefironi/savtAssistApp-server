"""reclamation_bitrix_outbox — retry-механизм при сбое синхронизации
рекламации с Bitrix24 (п.8 ТЗ). См. app/models/reclamation_bitrix_outbox.py.

Revision ID: c3f8e6a2d914
Revises: f9c3b7e14a82
Create Date: 2026-09-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c3f8e6a2d914"
down_revision: Union[str, None] = "f9c3b7e14a82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reclamation_bitrix_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reclamation_id", sa.Integer(),
            sa.ForeignKey("reclamations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "operation IN ('create', 'status', 'assignee')",
            name="ck_reclamation_bitrix_outbox_operation",
        ),
    )
    op.create_index(
        "ix_reclamation_bitrix_outbox_reclamation_id",
        "reclamation_bitrix_outbox", ["reclamation_id"],
    )
    op.create_index(
        "ix_reclamation_bitrix_outbox_operation",
        "reclamation_bitrix_outbox", ["operation"],
    )


def downgrade() -> None:
    op.drop_table("reclamation_bitrix_outbox")
