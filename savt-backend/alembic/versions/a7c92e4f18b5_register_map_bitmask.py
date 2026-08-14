"""битовая карта регистров: address+bit → название

Значение регистра — 16-битное слово, каждый бит потенциально своя авария
(не обязательно все биты именованы). Карта регистров была address→name (одно
значение целиком), теперь address+bit→name. У старых записей карты нет
осмысленного значения бита (никогда не хранился) — таблицы пересоздаются,
карту регистров нужно будет ввести заново через админку. "Текущее состояние"
(CabinetRegisterState) и сырая история (CabinetTelemetryEvent) не трогаются —
там как хранилось сырое число регистра, так и хранится, декодируется в биты
на чтение.

Revision ID: a7c92e4f18b5
Revises: f1a9c3e7b246
Create Date: 2026-08-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c92e4f18b5"
down_revision: Union[str, None] = "f1a9c3e7b246"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("cabinet_register_overrides")
    op.drop_index(op.f("ix_register_definitions_address"), table_name="register_definitions")
    op.drop_table("register_definitions")

    op.create_table(
        "register_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("address", sa.Integer(), nullable=False),
        sa.Column("bit", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("address", "bit", name="uq_register_definition_address_bit"),
    )
    op.create_index(op.f("ix_register_definitions_address"), "register_definitions", ["address"])

    op.create_table(
        "cabinet_register_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address", sa.Integer(), nullable=False),
        sa.Column("bit", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint(
            "cabinet_id", "address", "bit", name="uq_cabinet_register_override_cabinet_address_bit",
        ),
    )
    op.create_index(
        op.f("ix_cabinet_register_overrides_cabinet_id"), "cabinet_register_overrides", ["cabinet_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_cabinet_register_overrides_cabinet_id"), table_name="cabinet_register_overrides",
    )
    op.drop_table("cabinet_register_overrides")
    op.drop_index(op.f("ix_register_definitions_address"), table_name="register_definitions")
    op.drop_table("register_definitions")

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
