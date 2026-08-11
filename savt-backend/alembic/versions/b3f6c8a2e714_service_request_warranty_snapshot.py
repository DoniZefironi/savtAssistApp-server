"""заявки на обслуживание — снимок гарантии на момент создания (is_under_warranty)

Гарантийность заявки — платно/бесплатно — больше не смешивается с request_type
(видом работ: ремонт/диагностика/наладка). Это отдельный флаг, замороженный
на момент создания заявки: последующее изменение гарантии на ШУ/проекте задним
числом не должно менять уже открытые/закрытые заявки.

Бэкфилл существующих строк: сравниваем warranty_ends_at ШУ (или проекта — для
заявок по project_id) с created_at самой заявки. Это приближение (гарантию
могли позже продлить/сократить и на существующих заявках задним числом), но
единственный источник, которым можно воспользоваться постфактум.

Revision ID: b3f6c8a2e714
Revises: e4b7a1c9d635
Create Date: 2026-08-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3f6c8a2e714"
down_revision: Union[str, None] = "e4b7a1c9d635"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "service_requests",
        sa.Column("is_under_warranty", sa.Boolean(), nullable=True),
    )

    op.execute("""
        UPDATE service_requests sr
        SET is_under_warranty = (
            c.warranty_ends_at IS NOT NULL AND c.warranty_ends_at >= sr.created_at
        )
        FROM cabinets c
        WHERE sr.cabinet_id = c.id
    """)
    op.execute("""
        UPDATE service_requests sr
        SET is_under_warranty = (
            p.warranty_ends_at IS NOT NULL AND p.warranty_ends_at >= sr.created_at
        )
        FROM projects p
        WHERE sr.project_id = p.id
    """)

    op.alter_column("service_requests", "is_under_warranty", nullable=False)
    op.create_index(
        op.f("ix_service_requests_is_under_warranty"), "service_requests", ["is_under_warranty"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_service_requests_is_under_warranty"), table_name="service_requests")
    op.drop_column("service_requests", "is_under_warranty")
