"""nas_filename для cabinet_photos — обратная синхронизация фото с NAS

Revision ID: f4c7b1e9a382
Revises: d8f3a29c15be
Create Date: 2026-08-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4c7b1e9a382"
down_revision: Union[str, None] = "d8f3a29c15be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cabinet_photos", sa.Column("nas_filename", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("cabinet_photos", "nas_filename")
