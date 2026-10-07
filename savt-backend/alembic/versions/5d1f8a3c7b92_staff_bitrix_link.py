"""users.bitrix_user_id и users.must_change_password для сотрудников из Bitrix

Revision ID: 5d1f8a3c7b92
Revises: e4a7c1b9f206
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5d1f8a3c7b92"
down_revision: Union[str, None] = "e4a7c1b9f206"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("bitrix_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_users_bitrix_user_id", "users", ["bitrix_user_id"], unique=True)
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    op.drop_index("ix_users_bitrix_user_id", table_name="users")
    op.drop_column("users", "bitrix_user_id")
