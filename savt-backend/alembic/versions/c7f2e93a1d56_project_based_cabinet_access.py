"""доступ к ШУ выводится из проекта — убрать user_cabinets/cabinet_share_requests

Переход от "пользователь привязан к конкретным ШУ" (user_cabinets) к "доступ
к ШУ выводится из принадлежности к проекту" (user_projects + cabinets.project_id).

Что делает:
1. Создаёт cabinet_user_settings — личная персонализация ШУ (custom_name/comment),
   без какой-либо семантики доступа.
2. Добавляет cabinet_addition_requests.project_id (nullable — старые заявки его
   не несли).
3. Бэкфилл: переносит custom_name/custom_comment из user_cabinets в
   cabinet_user_settings; для каждой пары (пользователь, ШУ с проектом) из
   user_cabinets создаёт (если её ещё нет) запись в user_projects — доступ,
   который раньше был точечным, становится проектным.
4. Дропает user_cabinets и cabinet_share_requests целиком.

ВАЖНО ПЕРЕД ПРИМЕНЕНИЕМ НА ПРОДЕ: ШУ без project_id (deleted_at IS NULL) не
бэкфилятся в user_projects — их не к какому проекту прикрепить. Пользователи,
у которых доступ был только к таким шкафам, потеряют его после этой миграции.
Проверить заранее:
    SELECT id, object_number FROM cabinets WHERE project_id IS NULL AND deleted_at IS NULL;
Если список не пуст — привязать эти ШУ к проекту (PATCH /admin/cabinets/{id}/project)
до применения миграции.

Revision ID: c7f2e93a1d56
Revises: b6e1d5a94c72
Create Date: 2026-08-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7f2e93a1d56"
down_revision: Union[str, None] = "b6e1d5a94c72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cabinet_user_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("custom_name", sa.String(200), nullable=True),
        sa.Column("custom_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "cabinet_id", name="uq_cabinet_user_settings"),
    )
    op.create_index(
        op.f("ix_cabinet_user_settings_user_id"), "cabinet_user_settings", ["user_id"],
    )
    op.create_index(
        op.f("ix_cabinet_user_settings_cabinet_id"), "cabinet_user_settings", ["cabinet_id"],
    )

    op.add_column(
        "cabinet_addition_requests",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index(
        op.f("ix_cabinet_addition_requests_project_id"), "cabinet_addition_requests", ["project_id"],
    )

    # Бэкфилл персонализации
    op.execute("""
        INSERT INTO cabinet_user_settings (user_id, cabinet_id, custom_name, custom_comment, created_at)
        SELECT user_id, cabinet_id, custom_name, custom_comment, added_at
        FROM user_cabinets
        WHERE custom_name IS NOT NULL OR custom_comment IS NOT NULL
        ON CONFLICT (user_id, cabinet_id) DO NOTHING
    """)

    # Бэкфилл доступа: точечная привязка к ШУ -> членство в проекте этого ШУ.
    # ШУ без project_id сюда не попадают — см. предупреждение в докстринге выше.
    op.execute("""
        INSERT INTO user_projects (user_id, project_id, is_primary, added_at)
        SELECT DISTINCT uc.user_id, c.project_id, false, now()
        FROM user_cabinets uc
        JOIN cabinets c ON c.id = uc.cabinet_id
        WHERE c.project_id IS NOT NULL
        ON CONFLICT (user_id, project_id) DO NOTHING
    """)

    op.drop_table("user_cabinets")
    op.drop_table("cabinet_share_requests")


def downgrade() -> None:
    # Восстанавливаем структуру таблиц, но не данные бэкфилла — обратное
    # разворачивание "членство в проекте -> точечная привязка к ШУ" неоднозначно
    # (какие именно шкафы проекта считать привязанными задним числом).
    op.create_table(
        "cabinet_share_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id"), nullable=False),
        sa.Column("user_comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("admin_response", sa.Text(), nullable=True),
        sa.Column("resolved_by_admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_cabinets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("custom_name", sa.String(200), nullable=True),
        sa.Column("custom_comment", sa.Text(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "cabinet_id", name="uq_user_cabinet"),
    )
    op.create_index(
        "uq_user_cabinet_primary", "user_cabinets", ["cabinet_id"],
        unique=True, postgresql_where=sa.text("is_primary = true"),
    )

    op.drop_index(op.f("ix_cabinet_addition_requests_project_id"), table_name="cabinet_addition_requests")
    op.drop_column("cabinet_addition_requests", "project_id")

    op.drop_index(op.f("ix_cabinet_user_settings_cabinet_id"), table_name="cabinet_user_settings")
    op.drop_index(op.f("ix_cabinet_user_settings_user_id"), table_name="cabinet_user_settings")
    op.drop_table("cabinet_user_settings")
