"""проект: даты отгрузки, гарантия, компания и контакты из сделки Bitrix

Даты отгрузки, компания и контакты приезжают из карточки сделки и перезаписываются
на каждом ONCRMDEALUPDATE — в админке они только для чтения. Гарантия наоборот
наша: в Bitrix такого поля нет, ставит администратор. По ней же теперь решается,
попадает ли проект в ночную синхронизацию папок на NAS (см. is_sync_eligible).

documents.is_internal — документ, видимый только операторам и админам. В отличие
от requires_approval, где документ виден в списке и доступ можно запросить, этот
не показывается пользователям вовсе — ни в списке, ни названием. Для счетов,
договоров и прочей внутренней документации.

Revision ID: 229b04d4a392
Revises: 9edabb9982eb
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "229b04d4a392"
down_revision: Union[str, None] = "9edabb9982eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- поля проекта ---
    op.add_column("projects", sa.Column("shipment_planned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("shipment_actual_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("warranty_starts_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("warranty_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("bitrix_company_id", sa.String(20), nullable=True))
    op.add_column("projects", sa.Column("company_name", sa.String(255), nullable=True))
    # по ней отбираются проекты для ночной синхронизации папок
    op.create_index(op.f("ix_projects_warranty_ends_at"), "projects", ["warranty_ends_at"])

    # --- контакты заказчика из сделки ---
    op.create_table(
        "project_contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("bitrix_contact_id", sa.String(20), nullable=False),
        sa.Column("full_name", sa.String(300), nullable=True),
        sa.Column("post", sa.String(200), nullable=True),
        # у контакта обычно несколько телефонов (рабочий, мобильный) и почт
        sa.Column("phones", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("emails", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # пара уникальна — контакт обновляется на месте, а не дублируется
        sa.UniqueConstraint("project_id", "bitrix_contact_id", name="uq_project_contact"),
    )
    op.create_index(op.f("ix_project_contacts_project_id"), "project_contacts", ["project_id"])

    # --- служебные документы, видимые только сотрудникам ---
    op.add_column(
        "documents",
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("documents", "is_internal")
    op.drop_index(op.f("ix_project_contacts_project_id"), table_name="project_contacts")
    op.drop_table("project_contacts")
    op.drop_index(op.f("ix_projects_warranty_ends_at"), table_name="projects")
    for column in (
        "company_name", "bitrix_company_id",
        "warranty_ends_at", "warranty_starts_at",
        "shipment_actual_at", "shipment_planned_at",
    ):
        op.drop_column("projects", column)
