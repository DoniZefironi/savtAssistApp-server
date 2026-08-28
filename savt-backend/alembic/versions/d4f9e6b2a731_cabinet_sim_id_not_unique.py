"""cabinets.sim_id больше не уникален — одну и ту же SIM можно привязать
сразу к нескольким ШУ (бизнес-правило уточнено после первого использования
фичи, см. b3e5f8a1c294). Индекс остаётся, просто не unique — sim_id
по-прежнему ищется (обратный поиск "какие ШУ у этой SIM").

Revision ID: d4f9e6b2a731
Revises: c8a17d2f4b06
Create Date: 2026-08-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d4f9e6b2a731"
down_revision: Union[str, None] = "c8a17d2f4b06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_cabinets_sim_id"), table_name="cabinets")
    op.create_index(op.f("ix_cabinets_sim_id"), "cabinets", ["sim_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cabinets_sim_id"), table_name="cabinets")
    op.create_index(op.f("ix_cabinets_sim_id"), "cabinets", ["sim_id"], unique=True)
