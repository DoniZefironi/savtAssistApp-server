"""reclamations.bitrix_deleted_at — отметка, что карточку в Bitrix удалили.

Заявку при этом не удаляем и заново не заводим, только отвязываем и
показываем администратору интеграции. См. app/models/reclamation.py.

Revision ID: c5a71e93b8d4
Revises: b9e4a17d6c03
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c5a71e93b8d4"
down_revision: Union[str, None] = "b9e4a17d6c03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reclamations",
        sa.Column("bitrix_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reclamations", "bitrix_deleted_at")
