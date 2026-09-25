"""reclamation_bitrix_outbox: операция 'warranty' — отправка "Гарантии"
отдельным вызовом, никогда вместе со сменой стадии (совместная отправка
запускала на портале автозакрытие карточки, диагностировано и подтверждено
заказчиком 2026-09-25). См. app/models/reclamation_bitrix_outbox.py и
bitrix_service.update_reclamation_stage.

Revision ID: e8b21f4c9d63
Revises: a3f8c5d16e97
Create Date: 2026-09-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e8b21f4c9d63"
down_revision: Union[str, None] = "a3f8c5d16e97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_reclamation_bitrix_outbox_operation"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'deadline', 'warranty')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM reclamation_bitrix_outbox WHERE operation = 'warranty'")
    op.drop_constraint(_CONSTRAINT, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'deadline')",
    )
