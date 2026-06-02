"""add bot role

Revision ID: e2f4a8b1d095
Revises: c8e2d4f1a093
Create Date: 2026-05-27 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'e2f4a8b1d095'
down_revision = 'c8e2d4f1a093'
branch_labels = None
depends_on = None


def upgrade() -> None:
    roles_table = sa.table('roles', sa.column('id', sa.Integer), sa.column('name', sa.String))
    op.bulk_insert(roles_table, [{'id': 4, 'name': 'bot'}])


def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE name = 'bot'")
