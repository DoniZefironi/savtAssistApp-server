"""cabinets.sim_id: Integer -> String — SimApi использует GUID-строки как id
(не целые числа, как предполагалось в b3e5f8a1c294), выяснилось при первом
реальном вызове API. Колонка ещё пуста везде на проде (интеграция только
разворачивается), поэтому явное преобразование данных не нужно, но USING
всё равно указан — на случай, если это не так на момент применения.

Revision ID: c8a17d2f4b06
Revises: b3e5f8a1c294
Create Date: 2026-08-28 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8a17d2f4b06"
down_revision: Union[str, None] = "b3e5f8a1c294"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "cabinets", "sim_id",
        existing_type=sa.Integer(),
        type_=sa.String(length=64),
        postgresql_using="sim_id::text",
    )


def downgrade() -> None:
    op.alter_column(
        "cabinets", "sim_id",
        existing_type=sa.String(length=64),
        type_=sa.Integer(),
        postgresql_using="sim_id::integer",
    )
