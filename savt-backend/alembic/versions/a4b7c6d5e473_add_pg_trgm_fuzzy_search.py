"""add pg_trgm extension and normalize_search_text function for fuzzy search

Revision ID: a4b7c6d5e473
Revises: f2a6b5c4d362
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a4b7c6d5e473"
down_revision: Union[str, None] = "f2a6b5c4d362"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION normalize_search_text(input text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT trim(
                regexp_replace(
                    regexp_replace(lower(coalesce(input, '')), '[_\\-]+', ' ', 'g'),
                    '\\s+', ' ', 'g'
                )
            )
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS normalize_search_text(text)")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
