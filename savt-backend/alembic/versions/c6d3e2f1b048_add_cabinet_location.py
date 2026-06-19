"""add cabinet latitude longitude

Revision ID: c6d3e2f1b048
Revises: b5e2f1a3c049
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d3e2f1b048"
down_revision: Union[str, None] = "b5e2f1a3c049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cabinets", sa.Column("latitude", sa.Double(), nullable=True))
    op.add_column("cabinets", sa.Column("longitude", sa.Double(), nullable=True))


def downgrade() -> None:
    op.drop_column("cabinets", "longitude")
    op.drop_column("cabinets", "latitude")
