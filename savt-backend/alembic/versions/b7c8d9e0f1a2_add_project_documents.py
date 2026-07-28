"""add project_id to documents and document_requests, cabinet_id nullable

Revision ID: b7c8d9e0f1a2
Revises: f7a8b9c0d1e2
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("documents", "cabinet_id", nullable=True)
    op.add_column("documents", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_documents_project_id"), "documents", ["project_id"])
    op.create_foreign_key(
        "fk_documents_project_id_projects", "documents", "projects", ["project_id"], ["id"], ondelete="CASCADE"
    )
    op.create_check_constraint(
        "ck_documents_cabinet_xor_project",
        "documents",
        "(cabinet_id IS NOT NULL) != (project_id IS NOT NULL)",
    )

    op.alter_column("document_requests", "cabinet_id", nullable=True)
    op.add_column("document_requests", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_document_requests_project_id"), "document_requests", ["project_id"])
    op.create_foreign_key(
        "fk_document_requests_project_id_projects", "document_requests", "projects",
        ["project_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("fk_document_requests_project_id_projects", "document_requests", type_="foreignkey")
    op.drop_index(op.f("ix_document_requests_project_id"), table_name="document_requests")
    op.drop_column("document_requests", "project_id")
    op.alter_column("document_requests", "cabinet_id", nullable=False)

    op.drop_constraint("ck_documents_cabinet_xor_project", "documents", type_="check")
    op.drop_constraint("fk_documents_project_id_projects", "documents", type_="foreignkey")
    op.drop_index(op.f("ix_documents_project_id"), table_name="documents")
    op.drop_column("documents", "project_id")
    op.alter_column("documents", "cabinet_id", nullable=False)
