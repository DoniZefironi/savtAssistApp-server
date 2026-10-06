"""user_cabinets (прямое владение ШУ в обход проекта) + Cabinet.unique_code
(код для QR самостоятельного добавления ШУ, см. app/models/user_cabinet.py и
app/models/cabinets.py). Существующие ШУ получают код сразу, одним UPDATE —
новым ШУ код проставляет сервис при создании.

Revision ID: 7c2e9a4f1d83
Revises: 90c4a19b8f37
Create Date: 2026-10-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "7c2e9a4f1d83"
down_revision: Union[str, None] = "90c4a19b8f37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_cabinets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cabinet_id", sa.Integer(), sa.ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "cabinet_id", name="uq_user_cabinet"),
    )
    op.create_index("ix_user_cabinets_user_id", "user_cabinets", ["user_id"])
    op.create_index("ix_user_cabinets_cabinet_id", "user_cabinets", ["cabinet_id"])

    op.add_column("cabinets", sa.Column("unique_code", sa.String(length=32), nullable=True))
    op.execute(
        "UPDATE cabinets SET unique_code = substr(md5(random()::text || id::text), 1, 24) "
        "WHERE unique_code IS NULL"
    )
    op.create_index(
        op.f("ix_cabinets_unique_code"), "cabinets", ["unique_code"], unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_cabinets_unique_code"), table_name="cabinets")
    op.drop_column("cabinets", "unique_code")

    op.drop_index("ix_user_cabinets_cabinet_id", table_name="user_cabinets")
    op.drop_index("ix_user_cabinets_user_id", table_name="user_cabinets")
    op.drop_table("user_cabinets")
