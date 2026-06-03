"""add cabinet_tags table

Revision ID: b4d2e7f1a309
Revises: f3a1c9e2d078
Create Date: 2026-05-28 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'b4d2e7f1a309'
down_revision = 'f3a1c9e2d078'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'cabinet_tags',
        sa.Column('cabinet_id', sa.Integer(), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['cabinet_id'], ['cabinets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('cabinet_id', 'tag_id'),
    )


def downgrade() -> None:
    op.drop_table('cabinet_tags')
