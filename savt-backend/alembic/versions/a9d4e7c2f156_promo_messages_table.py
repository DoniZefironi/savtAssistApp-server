"""promo_messages — заготовки рекламных уведомлений переезжают из файла
(promo_messages.json, правился вручную на сервере) в БД, редактируются из
админки. Текущее содержимое файла (на момент этой миграции) переносится как
стартовые записи, чтобы ничего не потерять.

Заодно promo_schedule_settings.last_sent_message_id меняет тип со string на
integer — раньше ссылался на строковый id из файла, теперь на PromoMessage.id.
Таблица promo_schedule_settings ещё не была в проде на момент этой миграции
(заведена в предыдущей ревизии этой же сессии), поэтому просто пересоздаём
колонку, без переноса данных.

Revision ID: a9d4e7c2f156
Revises: f3c9a6e1b872
Create Date: 2026-09-11 00:00:00.000000
"""
import json
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import column, table

revision: str = "a9d4e7c2f156"
down_revision: Union[str, None] = "f3c9a6e1b872"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_FILE = Path(__file__).resolve().parent.parent.parent / "app" / "data" / "promo_messages.json"


def _load_legacy_messages() -> list[dict]:
    """Читает старый файл, если он есть на момент миграции — best-effort,
    отсутствие/битый файл не должно ронять миграцию, просто таблица
    останется пустой, завести записи можно будет и вручную из админки."""
    try:
        raw = json.loads(_LEGACY_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    result = []
    for item in raw.get("messages") or []:
        if not isinstance(item, dict):
            continue
        title, body = item.get("title"), item.get("body")
        if not title or not body:
            continue
        result.append({
            "title": str(title)[:255],
            "body": str(body)[:1000],
            "data": item.get("data") or {},
        })
    return result


def upgrade() -> None:
    op.create_table(
        "promo_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    legacy = _load_legacy_messages()
    if legacy:
        promo_messages = table(
            "promo_messages",
            column("title", sa.String),
            column("body", sa.String),
            column("data", postgresql.JSONB),
        )
        op.bulk_insert(promo_messages, legacy)

    op.drop_column("promo_schedule_settings", "last_sent_message_id")
    op.add_column(
        "promo_schedule_settings",
        sa.Column("last_sent_message_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("promo_schedule_settings", "last_sent_message_id")
    op.add_column(
        "promo_schedule_settings",
        sa.Column("last_sent_message_id", sa.String(64), nullable=True),
    )
    op.drop_table("promo_messages")
