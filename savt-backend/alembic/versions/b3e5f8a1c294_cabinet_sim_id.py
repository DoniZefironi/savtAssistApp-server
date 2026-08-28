"""ШУ может содержать сим-карту — sim_id ссылается на запись во внешнем
приложении управления SIM-картами (http://10.1.0.67:5000), не на таблицу в
этой БД. Сами данные SIM (статус/IP/телефон) там же и остаются — здесь
хранится только внешний id для связи, см. app/services/sim_service.py.

Revision ID: b3e5f8a1c294
Revises: a7c4e91f2b83
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3e5f8a1c294"
down_revision: Union[str, None] = "a7c4e91f2b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cabinets", sa.Column("sim_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_cabinets_sim_id"), "cabinets", ["sim_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_cabinets_sim_id"), table_name="cabinets")
    op.drop_column("cabinets", "sim_id")
