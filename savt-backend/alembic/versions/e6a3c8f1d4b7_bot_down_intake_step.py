"""пошаговый опрос при сбое бота

bot_down_intake_step — после сбоя Yandex API бот теперь задаёт уточняющие
вопросы по одному, а не все разом, чтобы проследить, что пользователь
ответил на каждый, прежде чем передать оператору. См.
app/services/bot_service.py.

Revision ID: e6a3c8f1d4b7
Revises: d1f4a8c2b957
Create Date: 2026-08-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6a3c8f1d4b7"
down_revision: Union[str, None] = "d1f4a8c2b957"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("bot_down_intake_step", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chats", "bot_down_intake_step")
