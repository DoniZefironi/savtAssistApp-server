"""пауза уведомлений: muted_until / muted_indefinitely

Пользователь может замолчать все пуши на 1/2/3/6/12/24 часа или бессрочно.
Две колонки вместо одной даты-«бесконечности»: бессрочная тишина — отдельное
состояние, а не очень далёкая дата, и по сентинелу его пришлось бы угадывать.

Revision ID: 2a838da52bdf
Revises: a835bebb7d46
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2a838da52bdf"
down_revision: Union[str, None] = "a835bebb7d46"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_settings",
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_settings",
        sa.Column(
            "muted_indefinitely", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_settings", "muted_indefinitely")
    op.drop_column("notification_settings", "muted_until")
