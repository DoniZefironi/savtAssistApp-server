"""add messenger_links and messenger_link_requests (Telegram/Viber code delivery)

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "messenger_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_chat_id", sa.String(64), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "channel", name="uq_messenger_links_user_channel"),
    )
    op.create_index(op.f("ix_messenger_links_user_id"), "messenger_links", ["user_id"])
    op.create_index(
        "ix_messenger_links_channel_chat_id", "messenger_links", ["channel", "external_chat_id"]
    )

    op.create_table(
        "messenger_link_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_messenger_link_requests_token"), "messenger_link_requests", ["token"], unique=True
    )
    op.create_index(op.f("ix_messenger_link_requests_user_id"), "messenger_link_requests", ["user_id"])
    op.create_index(op.f("ix_messenger_link_requests_phone"), "messenger_link_requests", ["phone"])
    op.create_index(
        op.f("ix_messenger_link_requests_expires_at"), "messenger_link_requests", ["expires_at"]
    )
    op.create_index(
        "ix_messenger_link_requests_user_channel", "messenger_link_requests", ["user_id", "channel"]
    )


def downgrade() -> None:
    op.drop_index("ix_messenger_link_requests_user_channel", table_name="messenger_link_requests")
    op.drop_index(op.f("ix_messenger_link_requests_expires_at"), table_name="messenger_link_requests")
    op.drop_index(op.f("ix_messenger_link_requests_phone"), table_name="messenger_link_requests")
    op.drop_index(op.f("ix_messenger_link_requests_user_id"), table_name="messenger_link_requests")
    op.drop_index(op.f("ix_messenger_link_requests_token"), table_name="messenger_link_requests")
    op.drop_table("messenger_link_requests")

    op.drop_index("ix_messenger_links_channel_chat_id", table_name="messenger_links")
    op.drop_index(op.f("ix_messenger_links_user_id"), table_name="messenger_links")
    op.drop_table("messenger_links")
