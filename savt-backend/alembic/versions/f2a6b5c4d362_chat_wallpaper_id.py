"""add wallpaper_id preset key to chat_user_settings

Revision ID: f2a6b5c4d362
Revises: e1f5a4b3c251
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a6b5c4d362"
down_revision: Union[str, None] = "e1f5a4b3c251"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_user_settings", sa.Column("wallpaper_id", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_user_settings", "wallpaper_id")
