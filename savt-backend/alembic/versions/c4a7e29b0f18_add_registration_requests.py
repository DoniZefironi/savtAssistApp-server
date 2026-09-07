"""registration_requests — заявка на регистрацию без Telegram-подтверждения
номера, с ручным одобрением администратором (см.
app/services/registration_request_service.py).

Revision ID: c4a7e29b0f18
Revises: b8f4a2e6c391
Create Date: 2026-09-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4a7e29b0f18"
down_revision: Union[str, None] = "b8f4a2e6c391"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "registration_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("user_type", sa.String(20), nullable=False),
        sa.Column("organization_name", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(20), nullable=True),
        sa.Column("user_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("admin_response", sa.Text(), nullable=True),
        sa.Column("resolved_by_admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_registration_requests_phone"), "registration_requests", ["phone"])
    op.create_index(op.f("ix_registration_requests_status"), "registration_requests", ["status"])
    op.create_index(
        op.f("ix_registration_requests_resolved_by_admin_id"), "registration_requests", ["resolved_by_admin_id"]
    )
    op.create_index(
        op.f("ix_registration_requests_created_user_id"), "registration_requests", ["created_user_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_registration_requests_created_user_id"), table_name="registration_requests")
    op.drop_index(op.f("ix_registration_requests_resolved_by_admin_id"), table_name="registration_requests")
    op.drop_index(op.f("ix_registration_requests_status"), table_name="registration_requests")
    op.drop_index(op.f("ix_registration_requests_phone"), table_name="registration_requests")
    op.drop_table("registration_requests")
