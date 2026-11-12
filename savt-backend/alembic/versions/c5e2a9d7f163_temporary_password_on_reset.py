"""Временный пароль по заявке на сброс: users.must_change_password, из заявки убран пароль

Revision ID: c5e2a9d7f163
Revises: a7c3f9e2b514
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e2a9d7f163"
down_revision: Union[str, None] = "a7c3f9e2b514"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), server_default="false", nullable=False),
    )
    op.drop_column("password_reset_requests", "hashed_password")


def downgrade() -> None:
    # Пароли из заявок не восстановить: колонка возвращается со значением, которое не
    # подходит ни к одному паролю
    op.add_column(
        "password_reset_requests",
        sa.Column("hashed_password", sa.String(255), server_default="!", nullable=False),
    )
    op.alter_column("password_reset_requests", "hashed_password", server_default=None)
    op.drop_column("users", "must_change_password")
