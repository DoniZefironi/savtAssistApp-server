"""заявки на обслуживание — разрешить привязку к проекту в целом, не только к ШУ

cabinet_id становится nullable, добавляется nullable project_id, CHECK — ровно
одно из двух заполнено. Та же схема, что уже применена для documents,
cabinet_photos и chats (ровно одна из cabinet_id/project_id).

Revision ID: e4b7a1c9d635
Revises: c7f2e93a1d56
Create Date: 2026-08-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b7a1c9d635"
down_revision: Union[str, None] = "c7f2e93a1d56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("service_requests", "cabinet_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("service_requests", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_service_requests_project_id"), "service_requests", ["project_id"], unique=False,
    )
    op.create_foreign_key(
        "fk_service_requests_project_id_projects", "service_requests", "projects", ["project_id"], ["id"],
    )
    op.create_check_constraint(
        "ck_service_request_cabinet_or_project",
        "service_requests",
        "(cabinet_id IS NOT NULL) != (project_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_service_request_cabinet_or_project", "service_requests", type_="check")
    op.drop_constraint("fk_service_requests_project_id_projects", "service_requests", type_="foreignkey")
    op.drop_index(op.f("ix_service_requests_project_id"), table_name="service_requests")
    op.drop_column("service_requests", "project_id")
    op.alter_column("service_requests", "cabinet_id", existing_type=sa.Integer(), nullable=False)
