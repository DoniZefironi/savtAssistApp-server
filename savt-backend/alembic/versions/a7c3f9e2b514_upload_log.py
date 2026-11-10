"""Журнал загрузок файлов для суточной квоты: upload_log

Revision ID: a7c3f9e2b514
Revises: d4a8e1b6c392
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3f9e2b514"
down_revision: Union[str, None] = "d4a8e1b6c392"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upload_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_upload_log_user_created", "upload_log", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_upload_log_user_created", table_name="upload_log")
    op.drop_table("upload_log")
