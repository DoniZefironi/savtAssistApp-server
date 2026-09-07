"""password_reset_requests — сброс пароля через одобрение администратором
для пользователей без Telegram (см.
app/services/password_reset_request_service.py).

Revision ID: d29f6c81a4e3
Revises: c4a7e29b0f18
Create Date: 2026-09-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d29f6c81a4e3"
down_revision: Union[str, None] = "c4a7e29b0f18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_reset_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("user_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("admin_response", sa.Text(), nullable=True),
        sa.Column("resolved_by_admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_password_reset_requests_user_id"), "password_reset_requests", ["user_id"])
    op.create_index(op.f("ix_password_reset_requests_status"), "password_reset_requests", ["status"])
    op.create_index(
        op.f("ix_password_reset_requests_resolved_by_admin_id"), "password_reset_requests", ["resolved_by_admin_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_password_reset_requests_resolved_by_admin_id"), table_name="password_reset_requests")
    op.drop_index(op.f("ix_password_reset_requests_status"), table_name="password_reset_requests")
    op.drop_index(op.f("ix_password_reset_requests_user_id"), table_name="password_reset_requests")
    op.drop_table("password_reset_requests")
