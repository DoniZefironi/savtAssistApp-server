"""подтверждение номера через Telegram request_contact; Viber удалён

Раньше владение номером не проверялось вообще: код уходил в тот Telegram, который
открыл deep-link, а введённый в форме номер с этим аккаунтом никак не сверялся —
занять можно было любой незарегистрированный номер. Теперь бот просит поделиться
контактом, и номер сверяется с заявленным.

Viber убран из каналов подтверждения: аналога request_contact у него нет, то есть
подтвердить номер через него нечем.

Revision ID: d3c17e880cd0
Revises: 3d03e47fd050
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3c17e880cd0"
down_revision: Union[str, None] = "3d03e47fd050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messenger_link_requests",
        sa.Column("external_chat_id", sa.String(64), nullable=True),
    )
    op.create_index(
        op.f("ix_messenger_link_requests_external_chat_id"),
        "messenger_link_requests",
        ["external_chat_id"],
    )

    # Незавершённые рукопожатия сделаны по старым правилам (одного /start было
    # достаточно) — гасим, чтобы ни одно из них не завершилось без проверки номера
    op.execute("UPDATE messenger_link_requests SET consumed_at = NOW() WHERE consumed_at IS NULL")

    # Viber больше не поддерживается. Связок быть не должно (бот никогда не был
    # настроен: при пустом VIBER_BOT_TOKEN проверка подписи вебхука всегда
    # отклоняла запрос, значит _complete_link для viber не отрабатывал ни разу),
    # но убираем на случай, если где-то остались.
    op.execute("DELETE FROM messenger_links WHERE channel = 'viber'")


def downgrade() -> None:
    op.drop_index(
        op.f("ix_messenger_link_requests_external_chat_id"),
        table_name="messenger_link_requests",
    )
    op.drop_column("messenger_link_requests", "external_chat_id")
