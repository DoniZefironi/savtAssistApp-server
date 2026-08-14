"""тумблер push-уведомлений об авариях ШУ

Новый переключатель cabinet_alarms в notification_settings — push при
появлении новой аварии (см. TelemetryIngestService.ingest), по умолчанию
включён. Не трогает cabinet_register_states/cabinet_telemetry_events.

Revision ID: c4e8b1f6a930
Revises: a7c92e4f18b5
Create Date: 2026-08-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e8b1f6a930"
down_revision: Union[str, None] = "a7c92e4f18b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_settings",
        sa.Column("cabinet_alarms", sa.Boolean(), server_default="true", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("notification_settings", "cabinet_alarms")
