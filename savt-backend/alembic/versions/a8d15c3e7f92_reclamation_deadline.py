"""reclamations.deadline_at — срок отработки рекламации, синхронизируется с
полем "Дедлайн" карточки Bitrix в обе стороны. Заодно разрешаем операцию
'deadline' в очереди повторов. См. app/models/reclamation.py.

Revision ID: a8d15c3e7f92
Revises: f2a94c7e6b13
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8d15c3e7f92"
down_revision: Union[str, None] = "f2a94c7e6b13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_reclamation_bitrix_outbox_operation"


def upgrade() -> None:
    op.add_column("reclamations", sa.Column("deadline_at", sa.Date(), nullable=True))

    op.drop_constraint(_CONSTRAINT, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'stage', 'deadline')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM reclamation_bitrix_outbox WHERE operation = 'deadline'")
    op.drop_constraint(_CONSTRAINT, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'stage')",
    )

    op.drop_column("reclamations", "deadline_at")
