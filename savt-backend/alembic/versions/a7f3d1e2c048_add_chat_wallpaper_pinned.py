"""add chat wallpaper and pinned message

Revision ID: a7f3d1e2c048
Revises: c1e5b8d2f047
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7f3d1e2c048"
down_revision: Union[str, None] = "c1e5b8d2f047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("wallpaper_url", sa.String(500), nullable=True))
    op.add_column("chats", sa.Column("pinned_message_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_chats_pinned_message_id",
        "chats", "messages",
        ["pinned_message_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_chats_pinned_message_id", "chats", type_="foreignkey")
    op.drop_column("chats", "pinned_message_id")
    op.drop_column("chats", "wallpaper_url")
