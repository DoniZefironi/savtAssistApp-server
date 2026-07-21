"""add projects, user_projects, project_share_requests and cabinets.project_id

Revision ID: d1e2f3a4b5c6
Revises: c9d1e0f8a695
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c9d1e0f8a695"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("unique_code", sa.String(100), nullable=False),
        sa.Column("parent_project_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_project_id"], ["projects.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_projects_unique_code"), "projects", ["unique_code"], unique=True)
    op.create_index(op.f("ix_projects_parent_project_id"), "projects", ["parent_project_id"])
    op.create_index(op.f("ix_projects_deleted_at"), "projects", ["deleted_at"])

    op.add_column("cabinets", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_cabinets_project_id"), "cabinets", ["project_id"])
    op.create_foreign_key(
        "fk_cabinets_project_id_projects", "cabinets", "projects", ["project_id"], ["id"], ondelete="SET NULL"
    )

    op.create_table(
        "user_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_user_project"),
    )
    op.create_index(op.f("ix_user_projects_user_id"), "user_projects", ["user_id"])
    op.create_index(op.f("ix_user_projects_project_id"), "user_projects", ["project_id"])
    op.create_index(
        "uq_user_project_primary", "user_projects", ["project_id"],
        unique=True, postgresql_where=sa.text("is_primary = true"),
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


def downgrade() -> None:
    op.drop_table("project_share_requests")

    op.drop_index("uq_user_project_primary", table_name="user_projects")
    op.drop_index(op.f("ix_user_projects_project_id"), table_name="user_projects")
    op.drop_index(op.f("ix_user_projects_user_id"), table_name="user_projects")
    op.drop_table("user_projects")

    op.drop_constraint("fk_cabinets_project_id_projects", "cabinets", type_="foreignkey")
    op.drop_index(op.f("ix_cabinets_project_id"), table_name="cabinets")
    op.drop_column("cabinets", "project_id")

    op.drop_index(op.f("ix_projects_deleted_at"), table_name="projects")
    op.drop_index(op.f("ix_projects_parent_project_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_unique_code"), table_name="projects")
    op.drop_table("projects")
