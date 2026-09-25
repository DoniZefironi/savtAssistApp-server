"""reclamations.responsible_bitrix_user_id — реальная колонка вместо разового
проброса assignedById в Bitrix "на лету". Нужна для двусторонней
синхронизации ответственного: раньше значение нигде не хранилось, поэтому
админка не могла ни предвыбрать текущего ответственного в дропдауне, ни
узнать о смене назначения, сделанной прямо в Bitrix. См. app/models/reclamation.py.

Revision ID: a3f8c5d16e97
Revises: d4e29a7c6b18
Create Date: 2026-09-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3f8c5d16e97"
down_revision: Union[str, None] = "d4e29a7c6b18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reclamations",
        sa.Column("responsible_bitrix_user_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reclamations", "responsible_bitrix_user_id")
