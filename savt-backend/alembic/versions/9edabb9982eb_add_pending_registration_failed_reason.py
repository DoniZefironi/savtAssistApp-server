"""pending_registrations.failed_reason — причина отказа для клиента

Отказ на шаге подтверждения номера виден только в Telegram, а приложение остаётся
на экране ввода кода и ждёт код, которого не будет. Теперь причина сохраняется и
отдаётся через GET /auth/register/status.

Revision ID: 9edabb9982eb
Revises: 987f377c75cf
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9edabb9982eb"
down_revision: Union[str, None] = "987f377c75cf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pending_registrations",
        sa.Column("failed_reason", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pending_registrations", "failed_reason")
