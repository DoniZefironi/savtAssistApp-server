"""documents.nas_filename — имя файла в папке проекта на NAS

Нужно для обратной синхронизации: файл, положенный в папку проекта напрямую,
подхватывается в приложение. Без запоминания фактического имени сверка была бы
неустойчивой — имя зеркала вычисляется как sanitize(title)+расширение, и если
исходное имя содержало символы, которые санитизация меняет, файл считался бы
новым при каждом прогоне и импортировался заново каждую ночь.

Revision ID: db59358b1bf0
Revises: 229b04d4a392
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "db59358b1bf0"
down_revision: Union[str, None] = "229b04d4a392"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("nas_filename", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "nas_filename")
