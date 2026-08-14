"""текущее состояние регистров ШУ (последнее значение на каждый адрес)

cabinet_register_states — по одной строке на (cabinet_id, address), значение
перезаписывается на каждое новое сообщение с этим адресом. В отличие от
cabinet_telemetry_events (сырая история для аудита) — не растёт бесконечно,
всегда актуальный снимок "что сейчас". GET /cabinets/{id}/telemetry теперь
читает отсюда, а не из истории.

Revision ID: f1a9c3e7b246
Revises: d4f7a0c39b21
Create Date: 2026-08-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a9c3e7b246"
down_revision: Union[str, None] = "d4f7a0c39b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cabinet_register_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address", sa.Integer(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("cabinet_id", "address", name="uq_cabinet_register_state_cabinet_address"),
    )
    op.create_index(
        op.f("ix_cabinet_register_states_cabinet_id"), "cabinet_register_states", ["cabinet_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_cabinet_register_states_cabinet_id"), table_name="cabinet_register_states")
    op.drop_table("cabinet_register_states")
