"""promotional по умолчанию включён — переключатель начал работать

Рассылки администратора до сих пор уходили всем, минуя переключатель
promotional: проверка настроек жила в NotificationService.send, а broadcast её
не вызывал. Теперь проверка есть.

Из-за этого нельзя просто начать проверять поле: у существующих пользователей
там false (прежний дефолт), и рассылки молча перестали бы доходить почти до
всех. Никто не мог осознанно выключить настройку, которая не работала, поэтому
приводим её к true — это сохраняет наблюдаемое поведение, а дальше пользователь
управляет ею сам.

Revision ID: a835bebb7d46
Revises: 63883505c79a
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a835bebb7d46"
down_revision: Union[str, None] = "63883505c79a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "notification_settings", "promotional",
        existing_type=sa.Boolean(), server_default=sa.text("true"),
    )
    op.execute("UPDATE notification_settings SET promotional = true WHERE promotional = false")


def downgrade() -> None:
    op.alter_column(
        "notification_settings", "promotional",
        existing_type=sa.Boolean(), server_default=sa.text("false"),
    )
