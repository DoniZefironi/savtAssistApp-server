"""Убрать users.bitrix_user_id и users.must_change_password

Синхронизация сотрудников из Bitrix удалена, колонки больше не нужны.

Revision ID: c2d9f4a7e153
Revises: 8b3e5c2d9a14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d9f4a7e153"
down_revision: Union[str, None] = "8b3e5c2d9a14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("users", "must_change_password")
    op.drop_index("ix_users_bitrix_user_id", table_name="users")
    op.drop_column("users", "bitrix_user_id")


def downgrade() -> None:
    op.add_column("users", sa.Column("bitrix_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_users_bitrix_user_id", "users", ["bitrix_user_id"], unique=True)
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), server_default="false", nullable=False),
    )
