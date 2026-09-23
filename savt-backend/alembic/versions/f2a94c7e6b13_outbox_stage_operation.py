"""reclamation_bitrix_outbox: операция 'stage' — ручной перевод карточки
между "Новая рекламация" и "На рассмотрении" из админки. Обе стадии = наш
статус review, поэтому отдельной операцией, а не через 'status'.
См. app/models/reclamation_bitrix_outbox.py.

Revision ID: f2a94c7e6b13
Revises: e7b3c95f2a48
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f2a94c7e6b13"
down_revision: Union[str, None] = "e7b3c95f2a48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_reclamation_bitrix_outbox_operation"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'stage')",
    )


def downgrade() -> None:
    # строки с новой операцией под старый констрейнт не подходят — убираем,
    # иначе create_check_constraint упадёт на уже лежащих данных
    op.execute("DELETE FROM reclamation_bitrix_outbox WHERE operation = 'stage'")
    op.drop_constraint(_CONSTRAINT, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee')",
    )
