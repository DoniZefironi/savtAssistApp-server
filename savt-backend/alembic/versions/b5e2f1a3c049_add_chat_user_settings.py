"""add chat_user_settings

Revision ID: b5e2f1a3c049
Revises: a7f3d1e2c048
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5e2f1a3c049"
down_revision: Union[str, None] = "a7f3d1e2c048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_user_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("own_bubble_color", sa.String(7), nullable=True),
        sa.Column("other_bubble_color", sa.String(7), nullable=True),
        sa.Column("bot_bubble_color", sa.String(7), nullable=True),
        sa.Column("own_text_color", sa.String(7), nullable=True),
        sa.Column("other_text_color", sa.String(7), nullable=True),
        sa.Column("bot_text_color", sa.String(7), nullable=True),
        sa.Column("nick_color", sa.String(7), nullable=True),
        sa.Column("font_size", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_user_settings_user_id", "chat_user_settings", ["user_id"])
    op.create_index("ix_chat_user_settings_chat_id", "chat_user_settings", ["chat_id"])
    # один глобальный профиль на пользователя
    op.create_index(
        "uq_user_global_settings", "chat_user_settings", ["user_id"],
        unique=True,
        postgresql_where=sa.text("chat_id IS NULL"),
    )
    # один per-chat профиль на пару (user_id, chat_id)
    op.create_index(
        "uq_user_chat_settings", "chat_user_settings", ["user_id", "chat_id"],
        unique=True,
        postgresql_where=sa.text("chat_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("chat_user_settings")
