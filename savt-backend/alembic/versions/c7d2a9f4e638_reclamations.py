"""reclamations / reclamation_attachments — фаза 1 (без Битрикса): подача и
ручная обработка рекламаций внутри Savt Assist. См. app/models/reclamation.py
и app/models/reclamation_attachment.py.

Revision ID: c7d2a9f4e638
Revises: b1e6f9a3c284
Create Date: 2026-09-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c7d2a9f4e638"
down_revision: Union[str, None] = "b1e6f9a3c284"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reclamations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),

        sa.Column("status", sa.String(20), nullable=False, server_default="review"),
        sa.Column("warranty_classification", sa.Boolean(), nullable=True),

        sa.Column("object_type", sa.String(20), nullable=False),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id"), nullable=True),
        sa.Column("object_details", postgresql.JSONB(), nullable=True),

        sa.Column("contract_number", sa.String(100), nullable=True),
        sa.Column("order_number", sa.String(100), nullable=True),
        sa.Column("ttn_number", sa.String(100), nullable=True),

        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurrence_conditions", sa.Text(), nullable=True),
        sa.Column("error_codes", sa.Text(), nullable=True),

        sa.Column("contact_name", sa.String(200), nullable=False),
        sa.Column("contact_phone", sa.String(20), nullable=False),
        sa.Column("contact_email", sa.String(100), nullable=False),
        sa.Column("customer_name", sa.String(255), nullable=True),

        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("resolution_comment", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("responsible_name", sa.String(200), nullable=True),
        sa.Column("responsible_phone", sa.String(20), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),

        sa.CheckConstraint(
            "object_type IN ('cabinet', 'line', 'component', 'software', 'documentation')",
            name="ck_reclamation_object_type",
        ),
        sa.CheckConstraint(
            "status IN ('review', 'in_progress', 'resolved', 'rejected')",
            name="ck_reclamation_status",
        ),
    )
    op.create_index("ix_reclamations_user_id", "reclamations", ["user_id"])
    op.create_index("ix_reclamations_status", "reclamations", ["status"])
    op.create_index("ix_reclamations_object_type", "reclamations", ["object_type"])
    op.create_index("ix_reclamations_cabinet_id", "reclamations", ["cabinet_id"])

    op.create_table(
        "reclamation_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reclamation_id", sa.Integer(),
            sa.ForeignKey("reclamations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("file_url", sa.String(500), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_reclamation_attachments_reclamation_id", "reclamation_attachments", ["reclamation_id"],
    )


def downgrade() -> None:
    op.drop_table("reclamation_attachments")
    op.drop_table("reclamations")
