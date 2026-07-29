"""add latitude/longitude to message_attachments for location sharing in chat

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("message_attachments", "file_url", existing_type=sa.String(500), nullable=True)
    op.alter_column("message_attachments", "file_name", existing_type=sa.String(255), nullable=True)
    op.alter_column("message_attachments", "file_size_bytes", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column("message_attachments", "mime_type", existing_type=sa.String(100), nullable=True)

    op.add_column("message_attachments", sa.Column("latitude", sa.Double(), nullable=True))
    op.add_column("message_attachments", sa.Column("longitude", sa.Double(), nullable=True))

    op.create_check_constraint(
        "ck_message_attachments_file_xor_location",
        "message_attachments",
        "(file_url IS NOT NULL) != (latitude IS NOT NULL AND longitude IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_message_attachments_file_xor_location", "message_attachments", type_="check"
    )
    op.drop_column("message_attachments", "longitude")
    op.drop_column("message_attachments", "latitude")

    op.alter_column("message_attachments", "mime_type", existing_type=sa.String(100), nullable=False)
    op.alter_column("message_attachments", "file_size_bytes", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("message_attachments", "file_name", existing_type=sa.String(255), nullable=False)
    op.alter_column("message_attachments", "file_url", existing_type=sa.String(500), nullable=False)
