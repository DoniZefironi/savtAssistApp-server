"""Исправление коллизии revision id: ревизия b1e6f9a3c284 в какой-то момент
существовала в двух разных редакциях под одним и тем же id (сначала — правка
CHECK на user_favorites под "избранные проекты", затем — колонки закрепа на
user_projects, когда решили делать через отдельное поле вместо user_favorites,
см. app/models/user_project.py). Alembic на боевой базе отметил ревизию
применённой ещё по первой редакции — вторая (настоящая) так и не выполнилась:
колонки user_projects.is_pinned/pinned_at не создались, а CHECK на
user_favorites остался расширенным значением 'project', которое по факту не
используется.

IF NOT EXISTS/IF EXISTS — чтобы миграция была безопасна независимо от того,
в каком из двух состояний база: на боевой (где текущее исправляется), и на
чистой/новой (где действующее содержимое b1e6f9a3c284 уже всё сделало
правильно и здесь просто не будет эффекта).

Revision ID: d4f8b2e91a56
Revises: c7d2a9f4e638
Create Date: 2026-09-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d4f8b2e91a56"
down_revision: Union[str, None] = "c7d2a9f4e638"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_projects ADD COLUMN IF NOT EXISTS is_pinned boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE user_projects ADD COLUMN IF NOT EXISTS pinned_at timestamptz"
    )
    op.drop_constraint("ck_user_favorite_entity_type", "user_favorites", type_="check")
    op.create_check_constraint(
        "ck_user_favorite_entity_type",
        "user_favorites",
        "entity_type IN ('document', 'kb_article', 'faq_entry')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_favorite_entity_type", "user_favorites", type_="check")
    op.create_check_constraint(
        "ck_user_favorite_entity_type",
        "user_favorites",
        "entity_type IN ('document', 'kb_article', 'faq_entry', 'project')",
    )
    op.execute("ALTER TABLE user_projects DROP COLUMN IF EXISTS pinned_at")
    op.execute("ALTER TABLE user_projects DROP COLUMN IF EXISTS is_pinned")
