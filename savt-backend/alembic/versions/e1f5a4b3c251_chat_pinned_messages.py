"""chat pinned messages (many-to-one)

Revision ID: e1f5a4b3c251
Revises: d9e4f3a2b150
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f5a4b3c251"
down_revision: Union[str, None] = "d9e4f3a2b150"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_pinned_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pinned_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_chat_pinned_message"),
    )
    op.create_index("ix_chat_pinned_messages_chat_id", "chat_pinned_messages", ["chat_id"])
    op.create_index("ix_chat_pinned_messages_message_id", "chat_pinned_messages", ["message_id"])

    op.drop_column("chats", "pinned_message_id")


def downgrade() -> None:
    op.add_column("chats", sa.Column("pinned_message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True))
    op.drop_index("ix_chat_pinned_messages_message_id", table_name="chat_pinned_messages")
    op.drop_index("ix_chat_pinned_messages_chat_id", table_name="chat_pinned_messages")
    op.drop_table("chat_pinned_messages")
