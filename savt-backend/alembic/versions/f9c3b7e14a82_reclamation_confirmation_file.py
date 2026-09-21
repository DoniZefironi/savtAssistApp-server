"""reclamations.confirmation_file_url/confirmation_file_name — подтверждающий
документ, обязателен при закрытии рекламации (resolved). Загружается через
общий POST /upload/attachment, отправляется в Bitrix (ufCrm53_1784725447065
"Подтверждающий документ" — обязательное поле на стадии SUCCESS, проверено
вживую). См. app/models/reclamation.py.

Revision ID: f9c3b7e14a82
Revises: e5a8f31c9d67
Create Date: 2026-09-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f9c3b7e14a82"
down_revision: Union[str, None] = "e5a8f31c9d67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reclamations",
        sa.Column("confirmation_file_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "reclamations",
        sa.Column("confirmation_file_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reclamations", "confirmation_file_name")
    op.drop_column("reclamations", "confirmation_file_url")
