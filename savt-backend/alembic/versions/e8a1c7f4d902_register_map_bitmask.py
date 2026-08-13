"""карта регистров — поддержка битовых масок

Часть регистров (аварии/неисправности) — не одно число, а 16-битная маска, где
у каждого бита своё название (типовая ПЛК-таблица "Неисправности": V650.00 —
"Авария РКФ", V650.01 — "Затопление" и т.п., всё в одном регистре 650).
Добавляем bit (NULL — регистр целиком одно значение, 0-15 — конкретный бит).

Revision ID: e8a1c7f4d902
Revises: d4f7a0c39b21
Create Date: 2026-08-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8a1c7f4d902"
down_revision: Union[str, None] = "d4f7a0c39b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("register_definitions", sa.Column("bit", sa.Integer(), nullable=True))
    # Раньше уникальность address была обычным unique-индексом (не constraint),
    # см. c2d5e9f8b613: op.create_index(..., unique=True)
    op.drop_index(op.f("ix_register_definitions_address"), table_name="register_definitions")
    op.create_index(
        op.f("ix_register_definitions_address"), "register_definitions", ["address"],
    )
    op.create_unique_constraint(
        "uq_register_definition_address_bit", "register_definitions", ["address", "bit"],
    )

    op.add_column("cabinet_register_overrides", sa.Column("bit", sa.Integer(), nullable=True))
    op.drop_constraint(
        "uq_cabinet_register_override_cabinet_address", "cabinet_register_overrides", type_="unique",
    )
    op.create_unique_constraint(
        "uq_cabinet_register_override_cabinet_address_bit",
        "cabinet_register_overrides", ["cabinet_id", "address", "bit"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_cabinet_register_override_cabinet_address_bit", "cabinet_register_overrides", type_="unique",
    )
    op.create_unique_constraint(
        "uq_cabinet_register_override_cabinet_address", "cabinet_register_overrides", ["cabinet_id", "address"],
    )
    op.drop_column("cabinet_register_overrides", "bit")

    op.drop_constraint(
        "uq_register_definition_address_bit", "register_definitions", type_="unique",
    )
    op.drop_index(op.f("ix_register_definitions_address"), table_name="register_definitions")
    op.create_index(
        op.f("ix_register_definitions_address"), "register_definitions", ["address"], unique=True,
    )
    op.drop_column("register_definitions", "bit")
