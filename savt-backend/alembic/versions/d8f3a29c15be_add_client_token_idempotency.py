"""client_token для идемпотентности сообщений и заявок (оффлайн-очередь клиента)

Revision ID: d8f3a29c15be
Revises: 2a838da52bdf
Create Date: 2026-08-07 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8f3a29c15be"
down_revision: Union[str, None] = "2a838da52bdf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("client_token", sa.String(64), nullable=True))
    op.create_index(
        "uq_messages_sender_client_token", "messages", ["sender_id", "client_token"],
        unique=True,
        postgresql_where=sa.text("client_token IS NOT NULL"),
    )

    op.add_column("service_requests", sa.Column("client_token", sa.String(64), nullable=True))
    op.create_index(
        "uq_service_requests_user_client_token", "service_requests", ["user_id", "client_token"],
        unique=True,
        postgresql_where=sa.text("client_token IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_service_requests_user_client_token", table_name="service_requests",
        postgresql_where=sa.text("client_token IS NOT NULL"),
    )
    op.drop_column("service_requests", "client_token")

    op.drop_index(
        "uq_messages_sender_client_token", table_name="messages",
        postgresql_where=sa.text("client_token IS NOT NULL"),
    )
    op.drop_column("messages", "client_token")
