"""pinned chats (personal, per-viewer)

Revision ID: 625d80a19751
Revises: b6f42a8d19e5
Create Date: 2026-09-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "625d80a19751"
down_revision: Union[str, None] = "b6f42a8d19e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pinned_chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "chat_id", name="uq_pinned_chat"),
    )
    op.create_index("ix_pinned_chats_user_id", "pinned_chats", ["user_id"])
    op.create_index("ix_pinned_chats_chat_id", "pinned_chats", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_pinned_chats_chat_id", table_name="pinned_chats")
    op.drop_index("ix_pinned_chats_user_id", table_name="pinned_chats")
    op.drop_table("pinned_chats")
