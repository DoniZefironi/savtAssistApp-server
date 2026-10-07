"""Закрепление ШУ пользователем: cabinet_user_settings.is_pinned / pinned_at

Revision ID: 8b3e5c2d9a14
Revises: 5d1f8a3c7b92
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8b3e5c2d9a14"
down_revision: Union[str, None] = "5d1f8a3c7b92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cabinet_user_settings",
        sa.Column("is_pinned", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "cabinet_user_settings",
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cabinet_user_settings", "pinned_at")
    op.drop_column("cabinet_user_settings", "is_pinned")
