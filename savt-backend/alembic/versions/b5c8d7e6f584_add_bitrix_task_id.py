"""add bitrix_task_id to service_requests

Revision ID: b5c8d7e6f584
Revises: a4b7c6d5e473
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c8d7e6f584"
down_revision: Union[str, None] = "a4b7c6d5e473"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("service_requests", sa.Column("bitrix_task_id", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("service_requests", "bitrix_task_id")
