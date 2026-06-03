"""add superadmin role

Revision ID: f3a1c9e2d078
Revises: e2f4a8b1d095
Create Date: 2026-05-28 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f3a1c9e2d078'
down_revision = 'e2f4a8b1d095'
branch_labels = None
depends_on = None


def upgrade() -> None:
    roles_table = sa.table('roles', sa.column('id', sa.Integer), sa.column('name', sa.String))
    op.bulk_insert(roles_table, [{'id': 5, 'name': 'superadmin'}])


def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE name = 'superadmin'")
