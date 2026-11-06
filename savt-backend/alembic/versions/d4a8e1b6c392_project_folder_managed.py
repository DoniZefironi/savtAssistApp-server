"""Папку проекта завёл сервер: projects.folder_managed

Revision ID: d4a8e1b6c392
Revises: c2d9f4a7e153
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a8e1b6c392"
down_revision: Union[str, None] = "c2d9f4a7e153"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("folder_managed", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("projects", "folder_managed")
