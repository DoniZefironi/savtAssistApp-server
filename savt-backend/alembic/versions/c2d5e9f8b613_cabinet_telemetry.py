"""телеметрия ШУ с MQTT-контроллеров через прокси на C#

Cabinet.mqtt_topic — топик контроллера этого ШУ ("26_001/1/data"), по нему
входящий вебхук находит cabinet_id (прокси про него не знает вообще).

register_definitions — стандартная карта регистров, общая для всех ШУ.
cabinet_register_overrides — добавки/переопределения карты на конкретный ШУ.
cabinet_telemetry_events — сырые входящие сообщения (регистр:значение),
расшифровка делается на чтение через карту выше, не при записи.

Revision ID: c2d5e9f8b613
Revises: b3f6c8a2e714
Create Date: 2026-08-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c2d5e9f8b613"
down_revision: Union[str, None] = "b3f6c8a2e714"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cabinets", sa.Column("mqtt_topic", sa.String(length=200), nullable=True))
    op.create_index(op.f("ix_cabinets_mqtt_topic"), "cabinets", ["mqtt_topic"], unique=True)

    op.create_table(
        "register_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("address", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        op.f("ix_register_definitions_address"), "register_definitions", ["address"], unique=True,
    )

    op.create_table(
        "cabinet_register_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("cabinet_id", "address", name="uq_cabinet_register_override_cabinet_address"),
    )
    op.create_index(
        op.f("ix_cabinet_register_overrides_cabinet_id"), "cabinet_register_overrides", ["cabinet_id"],
    )

    op.create_table(
        "cabinet_telemetry_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        op.f("ix_cabinet_telemetry_events_cabinet_id"), "cabinet_telemetry_events", ["cabinet_id"],
    )
    op.create_index(
        op.f("ix_cabinet_telemetry_events_received_at"), "cabinet_telemetry_events", ["received_at"],
    )


def downgrade() -> None:
    op.drop_table("cabinet_telemetry_events")
    op.drop_table("cabinet_register_overrides")
    op.drop_index(op.f("ix_register_definitions_address"), table_name="register_definitions")
    op.drop_table("register_definitions")
    op.drop_index(op.f("ix_cabinets_mqtt_topic"), table_name="cabinets")
    op.drop_column("cabinets", "mqtt_topic")
