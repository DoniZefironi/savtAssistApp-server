"""nas_mtime для cabinet_photos — детект замены файла на NAS

Revision ID: a29e6c4d8f13
Revises: f4c7b1e9a382
Create Date: 2026-08-10 00:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a29e6c4d8f13"
down_revision: Union[str, None] = "f4c7b1e9a382"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cabinet_photos", sa.Column("nas_mtime", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("cabinet_photos", "nas_mtime")
