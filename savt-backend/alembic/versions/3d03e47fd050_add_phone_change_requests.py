"""add phone_change_requests: смена номера только через одобрение админом

Самостоятельная смена номера (POST /auth/change-phone/start + /complete) убрана:
код доставлялся в мессенджер самого заявителя и владение новым номером не
подтверждал вообще, поэтому любой мог занять любой незарегистрированный номер.

Revision ID: 3d03e47fd050
Revises: b2c3d4e5f6a7
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3d03e47fd050"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "phone_change_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("new_phone", sa.String(20), nullable=False),
        sa.Column("old_phone", sa.String(20), nullable=True),
        sa.Column("user_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("admin_response", sa.Text(), nullable=True),
        sa.Column("resolved_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by_admin_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_phone_change_requests_user_id"), "phone_change_requests", ["user_id"])
    op.create_index(op.f("ix_phone_change_requests_new_phone"), "phone_change_requests", ["new_phone"])
    op.create_index(op.f("ix_phone_change_requests_status"), "phone_change_requests", ["status"])
    op.create_index(
        op.f("ix_phone_change_requests_resolved_by_admin_id"),
        "phone_change_requests",
        ["resolved_by_admin_id"],
    )

    # Незавершённые коды смены номера больше некому предъявить — эндпоинт удалён.
    # Гасим их, чтобы не висели активными до истечения TTL.
    op.execute(
        "UPDATE phone_verification_codes SET used_at = NOW() "
        "WHERE purpose = 'phone_change' AND used_at IS NULL"
    )
    op.execute(
        "UPDATE messenger_link_requests SET consumed_at = NOW() "
        "WHERE purpose = 'phone_change' AND consumed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_phone_change_requests_resolved_by_admin_id"), table_name="phone_change_requests")
    op.drop_index(op.f("ix_phone_change_requests_status"), table_name="phone_change_requests")
    op.drop_index(op.f("ix_phone_change_requests_new_phone"), table_name="phone_change_requests")
    op.drop_index(op.f("ix_phone_change_requests_user_id"), table_name="phone_change_requests")
    op.drop_table("phone_change_requests")
