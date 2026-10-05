"""reclamations.project_id — больше не обязателен для типов без ШУ.
Поле "Клиент" (компания/контакт) в Bitrix перестало быть обязательным при
создании элемента смарт-процесса (проверено вживую в форме создания —
звёздочкой отмечены только title/begindate/sourceDescription), поэтому
прежнее жёсткое "ровно одно из cabinet_id/project_id"
(см. d4e29a7c6b18_reclamation_project_id.py) смягчается до "не оба сразу" —
project_id для line/component/software/documentation теперь можно не
указывать вовсе.

Revision ID: 90c4a19b8f37
Revises: 625d80a19751
Create Date: 2026-10-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "90c4a19b8f37"
down_revision: Union[str, None] = "625d80a19751"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE reclamations DROP CONSTRAINT ck_reclamation_cabinet_or_project")
    op.execute(
        "ALTER TABLE reclamations ADD CONSTRAINT ck_reclamation_cabinet_or_project "
        "CHECK (NOT (cabinet_id IS NOT NULL AND project_id IS NOT NULL)) NOT VALID"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE reclamations DROP CONSTRAINT ck_reclamation_cabinet_or_project")
    op.execute(
        "ALTER TABLE reclamations ADD CONSTRAINT ck_reclamation_cabinet_or_project "
        "CHECK ((cabinet_id IS NOT NULL) != (project_id IS NOT NULL)) NOT VALID"
    )
