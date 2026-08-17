"""детерминированное состояние эскалации оператора в чате бота

bot_offered_operator — бот сам только что явно предложил позвать оператора
(готовой фразой после исчерпания попыток), следующий ответ пользователя
трактуется как согласие/отказ безусловно. operator_insist_count — счётчик
незапрошенных прямых просьб оператора подряд (бот не предлагал сам) — пока
< 2, бот настаивает на своей помощи вместо немедленной передачи. См.
app/services/bot_service.py.

Revision ID: d1f4a8c2b957
Revises: c4e8b1f6a930
Create Date: 2026-08-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1f4a8c2b957"
down_revision: Union[str, None] = "c4e8b1f6a930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("bot_offered_operator", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "chats",
        sa.Column("operator_insist_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chats", "operator_insist_count")
    op.drop_column("chats", "bot_offered_operator")
