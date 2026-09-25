"""reclamation_bitrix_outbox: операция 'comment' — коренная причина, итоговый
комментарий и причина отклонения уходят в таймлайн карточки Bitrix
(crm.timeline.comment.add), потому что своего UF-поля под эти три текстовых
поля в смарт-процессе нет. См. app/models/reclamation_bitrix_outbox.py и
bitrix_service.add_reclamation_comment.

Revision ID: b6f42a8d19e5
Revises: e8b21f4c9d63
Create Date: 2026-09-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b6f42a8d19e5"
down_revision: Union[str, None] = "e8b21f4c9d63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_reclamation_bitrix_outbox_operation"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'deadline', 'warranty', 'comment')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM reclamation_bitrix_outbox WHERE operation = 'comment'")
    op.drop_constraint(_CONSTRAINT, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'deadline', 'warranty')",
    )
