"""service request chats and chat archiving

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("service_request_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_chats_service_request_id", "chats", "service_requests",
        ["service_request_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index(
        "uq_chat_service_request", "chats", ["service_request_id"],
        unique=True, postgresql_where=sa.text("service_request_id IS NOT NULL"),
    )

    op.add_column("chats", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_chats_archived_at"), "chats", ["archived_at"])

    # ck_chat_type исторически объявлен только в модели (app/models/chat.py),
    # ни одна прошлая миграция его реально не создавала — на проде констрейнта
    # может не быть вовсе, поэтому дропаем через IF EXISTS вместо op.drop_constraint
    op.execute("ALTER TABLE chats DROP CONSTRAINT IF EXISTS ck_chat_type")
    op.create_check_constraint(
        "ck_chat_type", "chats", "chat_type IN ('cabinet', 'support', 'notes', 'service_request')",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chats DROP CONSTRAINT IF EXISTS ck_chat_type")
    op.create_check_constraint(
        "ck_chat_type", "chats", "chat_type IN ('cabinet', 'support', 'notes')",
    )

    op.drop_index(op.f("ix_chats_archived_at"), table_name="chats")
    op.drop_column("chats", "archived_at")

    op.drop_index("uq_chat_service_request", table_name="chats")
    op.drop_constraint("fk_chats_service_request_id", "chats", type_="foreignkey")
    op.drop_column("chats", "service_request_id")
