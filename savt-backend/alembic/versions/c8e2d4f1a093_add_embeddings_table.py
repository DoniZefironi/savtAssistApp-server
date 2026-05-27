"""add embeddings table with pgvector

Revision ID: c8e2d4f1a093
Revises: a3f9c2e1b047
Create Date: 2026-05-27 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'c8e2d4f1a093'
down_revision = 'a3f9c2e1b047'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        'embeddings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_type', sa.String(length=20), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', sa.Text(), nullable=False),  # placeholder, altered below
        sa.Column('meta', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # Replace the Text placeholder with the actual vector(256) type
    op.execute("ALTER TABLE embeddings ALTER COLUMN embedding TYPE vector(256) USING embedding::vector(256)")

    op.create_index(
        'ix_embeddings_source',
        'embeddings',
        ['source_type', 'source_id'],
    )

    # HNSW index for fast approximate nearest-neighbour search
    op.execute(
        "CREATE INDEX ix_embeddings_hnsw ON embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_hnsw")
    op.drop_index('ix_embeddings_source', table_name='embeddings')
    op.drop_table('embeddings')
