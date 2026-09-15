"""favorite projects — расширяет user_favorites.entity_type значением
'project', чтобы пользователь мог закреплять проекты в избранном (тот же
общий механизм, что уже используется для документов/статей базы знаний/FAQ,
см. app/routers/favorites.py).

Revision ID: b1e6f9a3c284
Revises: a9d4e7c2f156
Create Date: 2026-09-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b1e6f9a3c284"
down_revision: Union[str, None] = "a9d4e7c2f156"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_user_favorite_entity_type", "user_favorites", type_="check")
    op.create_check_constraint(
        "ck_user_favorite_entity_type",
        "user_favorites",
        "entity_type IN ('document', 'kb_article', 'faq_entry', 'project')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_favorite_entity_type", "user_favorites", type_="check")
    op.create_check_constraint(
        "ck_user_favorite_entity_type",
        "user_favorites",
        "entity_type IN ('document', 'kb_article', 'faq_entry')",
    )
