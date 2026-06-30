"""move wallpaper_url from chats to chat_user_settings

Revision ID: d9e4f3a2b150
Revises: c6d3e2f1b048
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9e4f3a2b150"
down_revision: Union[str, None] = "c6d3e2f1b048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_user_settings", sa.Column("wallpaper_url", sa.String(500), nullable=True))
    op.drop_column("chats", "wallpaper_url")


def downgrade() -> None:
    op.add_column("chats", sa.Column("wallpaper_url", sa.String(500), nullable=True))
    op.drop_column("chat_user_settings", "wallpaper_url")
