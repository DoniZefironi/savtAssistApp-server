"""user_projects.is_pinned/pinned_at — закреп проекта наверх списка
GET /projects, управляется через POST/DELETE /projects/{id}/pin. Живёт прямо
на членстве (не в user_favorites), т.к. закреп имеет смысл только для
проекта, к которому есть доступ, и должен пропадать сам при выходе из
проекта — без отдельной чистки.

Revision ID: b1e6f9a3c284
Revises: a9d4e7c2f156
Create Date: 2026-09-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b1e6f9a3c284"
down_revision: Union[str, None] = "a9d4e7c2f156"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_projects",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "user_projects",
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_projects", "pinned_at")
    op.drop_column("user_projects", "is_pinned")
