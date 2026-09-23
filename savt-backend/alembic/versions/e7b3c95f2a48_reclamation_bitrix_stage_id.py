"""reclamations.bitrix_stage_id — реальная стадия карточки в Bitrix.

Стадий в смарт-процессе шесть, наших статусов четыре: "Новая рекламация" и
"На рассмотрении" оба схлопываются в review, "Отклонена" и "Ошибочные
рекламации" — в rejected. Заявителю показываем только status, админке —
ещё и эту колонку. См. app/models/reclamation.py.

Revision ID: e7b3c95f2a48
Revises: c3f8e6a2d914
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e7b3c95f2a48"
down_revision: Union[str, None] = "c3f8e6a2d914"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reclamations",
        sa.Column("bitrix_stage_id", sa.String(50), nullable=True),
    )
    # у уже созданных рекламаций, которые доехали до Bitrix, карточка
    # заводится на "Новая рекламация" — проставляем её, чтобы в админке не
    # висел прочерк там, где стадия на самом деле известна
    op.execute(
        "UPDATE reclamations SET bitrix_stage_id = 'DT1176_69:NEW' "
        "WHERE bitrix_item_id IS NOT NULL AND status = 'review'"
    )


def downgrade() -> None:
    op.drop_column("reclamations", "bitrix_stage_id")
