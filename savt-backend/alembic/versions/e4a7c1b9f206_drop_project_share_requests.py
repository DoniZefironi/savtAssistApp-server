"""убрать заявки на вступление в проект (project_share_requests) и
UserProject.is_primary — любое число пользователей состоит в проекте без
чьего-либо одобрения и без "главного" участника, см.
app/services/user_project_service.py UserProjectService.add_by_qr.

Revision ID: e4a7c1b9f206
Revises: 7c2e9a4f1d83
Create Date: 2026-10-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e4a7c1b9f206"
down_revision: Union[str, None] = "7c2e9a4f1d83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_project_share_requests_resolved_by_admin_id"), table_name="project_share_requests")
    op.drop_index(op.f("ix_project_share_requests_status"), table_name="project_share_requests")
    op.drop_index(op.f("ix_project_share_requests_project_id"), table_name="project_share_requests")
    op.drop_index(op.f("ix_project_share_requests_user_id"), table_name="project_share_requests")
    op.drop_table("project_share_requests")

    op.drop_index("uq_user_project_primary", table_name="user_projects")
    op.drop_column("user_projects", "is_primary")


def downgrade() -> None:
    op.add_column(
        "user_projects",
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index(
        "uq_user_project_primary", "user_projects", ["project_id"], unique=True,
        postgresql_where=sa.text("is_primary = true"),
    )

    op.create_table(
        "project_share_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("admin_response", sa.Text(), nullable=True),
        sa.Column("resolved_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["resolved_by_admin_id"], ["users.id"]),
    )
    op.create_index(op.f("ix_project_share_requests_user_id"), "project_share_requests", ["user_id"])
    op.create_index(op.f("ix_project_share_requests_project_id"), "project_share_requests", ["project_id"])
    op.create_index(op.f("ix_project_share_requests_status"), "project_share_requests", ["status"])
    op.create_index(
        op.f("ix_project_share_requests_resolved_by_admin_id"), "project_share_requests", ["resolved_by_admin_id"]
    )
