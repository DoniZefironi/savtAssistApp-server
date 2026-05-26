"""add is_verified to users, faq_entry to favorites

Revision ID: a3f9c2e1b047
Revises: 6a5a3918aa88
Create Date: 2026-05-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f9c2e1b047'
down_revision: Union[str, None] = '6a5a3918aa88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_verified', sa.Boolean(), server_default='false', nullable=False))

    op.drop_constraint('ck_user_favorite_entity_type', 'user_favorites', type_='check')
    op.create_check_constraint(
        'ck_user_favorite_entity_type',
        'user_favorites',
        "entity_type IN ('document', 'kb_article', 'faq_entry')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_user_favorite_entity_type', 'user_favorites', type_='check')
    op.create_check_constraint(
        'ck_user_favorite_entity_type',
        'user_favorites',
        "entity_type IN ('document', 'kb_article')",
    )

    op.drop_column('users', 'is_verified')
