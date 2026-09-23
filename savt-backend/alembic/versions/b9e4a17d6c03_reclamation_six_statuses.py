"""Статусы рекламации один к одному со стадиями смарт-процесса Bitrix.

Было четыре статуса, стало шесть: "Новая рекламация" и "На рассмотрении"
схлопывались в review, "Отклонена" и "Ошибочные рекламации" — в rejected,
из-за чего обратная синхронизация была принципиально неполной (из Bitrix
уже не восстановить, какая из двух стадий имелась в виду).

Существующие записи раскладываем по bitrix_stage_id — ради этого колонка тут
и нужна; после раскладки она становится дублем status и удаляется.

Revision ID: b9e4a17d6c03
Revises: a8d15c3e7f92
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b9e4a17d6c03"
down_revision: Union[str, None] = "a8d15c3e7f92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_CK = "ck_reclamation_status"
_OUTBOX_CK = "ck_reclamation_bitrix_outbox_operation"


def upgrade() -> None:
    # 1. Сначала расширяем CHECK — иначе UPDATE ниже сам себя не пустит
    op.drop_constraint(_STATUS_CK, "reclamations", type_="check")
    op.create_check_constraint(
        _STATUS_CK,
        "reclamations",
        "status IN ('new', 'review', 'in_progress', 'resolved', 'rejected', 'invalid')",
    )

    # 2. Раскладываем существующие записи по реальной стадии в Bitrix.
    # review -> new везде, кроме тех, что реально стоят на "На рассмотрении".
    # Не доехавшие до Bitrix (bitrix_stage_id IS NULL) — это только что
    # поданные заявки, им место в new.
    op.execute(
        "UPDATE reclamations SET status = 'new' "
        "WHERE status = 'review' "
        "AND (bitrix_stage_id IS NULL OR bitrix_stage_id <> 'DT1176_69:UC_DPZ1YJ')"
    )
    op.execute(
        "UPDATE reclamations SET status = 'invalid' "
        "WHERE status = 'rejected' AND bitrix_stage_id = 'DT1176_69:FAIL'"
    )

    # 3. Новая заявка теперь заводится как new, а не review
    op.alter_column("reclamations", "status", server_default="new")

    # 4. Стадия стала дублем статуса — колонка больше не нужна
    op.drop_column("reclamations", "bitrix_stage_id")

    # 5. Вместе с ней уходит и ручной перевод стадии из админки
    op.execute("DELETE FROM reclamation_bitrix_outbox WHERE operation = 'stage'")
    op.drop_constraint(_OUTBOX_CK, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _OUTBOX_CK,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'deadline')",
    )


def downgrade() -> None:
    op.drop_constraint(_OUTBOX_CK, "reclamation_bitrix_outbox", type_="check")
    op.create_check_constraint(
        _OUTBOX_CK,
        "reclamation_bitrix_outbox",
        "operation IN ('create', 'status', 'assignee', 'stage', 'deadline')",
    )

    op.add_column(
        "reclamations", sa.Column("bitrix_stage_id", sa.String(50), nullable=True)
    )
    # восстанавливаем стадию из статуса — соответствие один к одному, так что
    # обратный разбор точный
    op.execute(
        "UPDATE reclamations SET bitrix_stage_id = CASE status "
        "WHEN 'new' THEN 'DT1176_69:NEW' "
        "WHEN 'review' THEN 'DT1176_69:UC_DPZ1YJ' "
        "WHEN 'in_progress' THEN 'DT1176_69:CLIENT' "
        "WHEN 'resolved' THEN 'DT1176_69:SUCCESS' "
        "WHEN 'rejected' THEN 'DT1176_69:UC_RNKN52' "
        "WHEN 'invalid' THEN 'DT1176_69:FAIL' END "
        "WHERE bitrix_item_id IS NOT NULL"
    )

    op.execute("UPDATE reclamations SET status = 'review' WHERE status = 'new'")
    op.execute("UPDATE reclamations SET status = 'rejected' WHERE status = 'invalid'")
    op.alter_column("reclamations", "status", server_default="review")

    op.drop_constraint(_STATUS_CK, "reclamations", type_="check")
    op.create_check_constraint(
        _STATUS_CK,
        "reclamations",
        "status IN ('review', 'in_progress', 'resolved', 'rejected')",
    )
