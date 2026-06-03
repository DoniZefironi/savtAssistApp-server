"""add scope to tags

Revision ID: c1e5b8d2f047
Revises: b4d2e7f1a309
Create Date: 2026-05-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'c1e5b8d2f047'
down_revision = 'b4d2e7f1a309'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tags', sa.Column('scope', sa.String(length=20), nullable=False, server_default='document'))
    op.create_index('ix_tags_scope', 'tags', ['scope'])

    # Снимаем старый уникальный индекс только по name
    op.drop_index('ix_tags_name', table_name='tags')

    # Составной уникальный ключ (name, scope)
    op.create_unique_constraint('uq_tag_name_scope', 'tags', ['name', 'scope'])


def downgrade() -> None:
    op.drop_constraint('uq_tag_name_scope', 'tags', type_='unique')
    op.create_index('ix_tags_name', 'tags', ['name'], unique=True)
    op.drop_index('ix_tags_scope', table_name='tags')
    op.drop_column('tags', 'scope')
