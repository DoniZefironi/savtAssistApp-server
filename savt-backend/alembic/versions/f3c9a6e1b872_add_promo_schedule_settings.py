"""promo_schedule_settings — управляемое из админки расписание рекламных
уведомлений (интервал в днях, час, набор заготовок), вместо
PROMO_AUTO_SEND_HOUR из .env. См. app/services/promo_service.py.

Revision ID: f3c9a6e1b872
Revises: e7b3f0a4d5c2
Create Date: 2026-09-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f3c9a6e1b872"
down_revision: Union[str, None] = "e7b3f0a4d5c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promo_schedule_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("send_hour", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("message_ids", postgresql.JSONB(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_message_id", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("promo_schedule_settings")
