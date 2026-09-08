"""cabinets.folder_name — отслеживание реального имени подпапок ШУ на NAS,
чтобы переносить их при смене номера объекта/внутреннего названия (см.
app/services/project_folder_service.py _relocate_cabinet_structure).

Revision ID: e7b3f0a4d5c2
Revises: d29f6c81a4e3
Create Date: 2026-09-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e7b3f0a4d5c2"
down_revision: Union[str, None] = "d29f6c81a4e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cabinets", sa.Column("folder_name", sa.String(160), nullable=True))


def downgrade() -> None:
    op.drop_column("cabinets", "folder_name")
