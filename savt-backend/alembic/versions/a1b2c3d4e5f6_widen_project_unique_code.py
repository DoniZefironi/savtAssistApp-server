"""widen projects.unique_code to fit Fernet-encrypted project codes from Bitrix

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "projects", "unique_code",
        existing_type=sa.String(100), type_=sa.String(200), existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "projects", "unique_code",
        existing_type=sa.String(200), type_=sa.String(100), existing_nullable=False,
    )
