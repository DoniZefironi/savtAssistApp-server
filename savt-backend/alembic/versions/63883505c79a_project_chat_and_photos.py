"""чат и фотографии у проекта — как у ШУ

Проект становится самостоятельной сущностью: у него появляется собственный чат и
собственные фотографии, то есть всё то же, что было только у ШУ. Сами ШУ остаются
работать как прежде.

cabinet_photos переименована не была намеренно: таблица теперь хранит и фото ШУ, и
фото проектов (ровно одно из двух, как у documents), но переименование потребовало
бы трогать все существующие запросы ради косметики.

Revision ID: 63883505c79a
Revises: db59358b1bf0
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "63883505c79a"
down_revision: Union[str, None] = "db59358b1bf0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- чат проекта ---
    op.add_column("chats", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_chats_project_id", "chats", "projects", ["project_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index(op.f("ix_chats_project_id"), "chats", ["project_id"])

    # Один project-чат на пару пользователь + проект — так же, как у ШУ
    op.create_index(
        "uq_user_project_chat", "chats", ["user_id", "project_id"], unique=True,
        postgresql_where=sa.text("chat_type = 'project' AND project_id IS NOT NULL"),
    )

    op.drop_constraint("ck_chat_type", "chats", type_="check")
    op.create_check_constraint(
        "ck_chat_type", "chats",
        "chat_type IN ('cabinet', 'support', 'notes', 'service_request', 'project')",
    )

    # --- фотографии проекта ---
    op.alter_column("cabinet_photos", "cabinet_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("cabinet_photos", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_cabinet_photos_project_id", "cabinet_photos", "projects",
        ["project_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index(op.f("ix_cabinet_photos_project_id"), "cabinet_photos", ["project_id"])
    # Ровно одно из двух — та же логика, что у documents
    op.create_check_constraint(
        "ck_photo_cabinet_or_project", "cabinet_photos",
        "(cabinet_id IS NOT NULL) != (project_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_photo_cabinet_or_project", "cabinet_photos", type_="check")
    op.drop_index(op.f("ix_cabinet_photos_project_id"), table_name="cabinet_photos")
    op.drop_constraint("fk_cabinet_photos_project_id", "cabinet_photos", type_="foreignkey")
    op.drop_column("cabinet_photos", "project_id")
    # Вернуть NOT NULL можно только если проектных фото не осталось
    op.execute("DELETE FROM cabinet_photos WHERE cabinet_id IS NULL")
    op.alter_column("cabinet_photos", "cabinet_id", existing_type=sa.Integer(), nullable=False)

    op.drop_constraint("ck_chat_type", "chats", type_="check")
    op.create_check_constraint(
        "ck_chat_type", "chats",
        "chat_type IN ('cabinet', 'support', 'notes', 'service_request')",
    )
    op.drop_index("uq_user_project_chat", table_name="chats")
    op.drop_index(op.f("ix_chats_project_id"), table_name="chats")
    op.drop_constraint("fk_chats_project_id", "chats", type_="foreignkey")
    op.drop_column("chats", "project_id")
