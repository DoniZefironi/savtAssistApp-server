"""add projects.production_number for idempotent Bitrix deal import

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("production_number", sa.String(50), nullable=True))
    op.create_index(
        op.f("ix_projects_production_number"), "projects", ["production_number"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_projects_production_number"), table_name="projects")
    op.drop_column("projects", "production_number")
