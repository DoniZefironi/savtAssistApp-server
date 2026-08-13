"""брокер MQTT — свой у каждого ШУ, не общий на всех

Раньше host/port MQTT-брокера предполагались одним общим значением на весь
telemetry-proxy (см. c2d5e9f8b613) — оказалось неверно: у каждого контроллера
свой брокер (свой IP), общий на всех topic-фильтр тут не поможет. Хост/порт
(и опциональные логин/пароль, у части контроллеров есть аутентификация)
переезжают на конкретный Cabinet — прокси узнаёт актуальный список брокеров
у бэкенда (GET /webhooks/telemetry/targets), а не хранит его в своём конфиге.

Revision ID: d4f7a0c39b21
Revises: c2d5e9f8b613
Create Date: 2026-08-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4f7a0c39b21"
down_revision: Union[str, None] = "c2d5e9f8b613"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cabinets", sa.Column("mqtt_host", sa.String(length=255), nullable=True))
    op.add_column("cabinets", sa.Column("mqtt_port", sa.Integer(), nullable=True))
    op.add_column("cabinets", sa.Column("mqtt_username", sa.String(length=200), nullable=True))
    op.add_column("cabinets", sa.Column("mqtt_password", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("cabinets", "mqtt_password")
    op.drop_column("cabinets", "mqtt_username")
    op.drop_column("cabinets", "mqtt_port")
    op.drop_column("cabinets", "mqtt_host")
