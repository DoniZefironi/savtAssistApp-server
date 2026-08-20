"""GIN-индексы под нечёткий поиск в audit_logs

AuditRepository.list_logs искал ILIKE '%...%' без индекса — переведён на
fuzzy_condition (normalize_search_text + pg_trgm), как остальные репозитории
(см. d3f8b2c6a915). Индексы — по тому же выражению, что строит поиск;
actor_name (User.full_name) уже покрыт ix_trgm_users_full_name из той же
миграции, здесь не дублируется.

Таблица растёт (по одной записи на каждое CUD-действие, ~600 точек вызова) —
без индекса поиск по payload/action/entity_type деградирует линейно с числом
строк уже на десятках тысяч записей.

Revision ID: a7c4e91f2b83
Revises: d3f8b2c6a915
Create Date: 2026-08-20 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a7c4e91f2b83"
down_revision: Union[str, None] = "d3f8b2c6a915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = [
    ("ix_trgm_audit_logs_action", "audit_logs", "action"),
    ("ix_trgm_audit_logs_entity_type", "audit_logs", "entity_type"),
    ("ix_trgm_audit_logs_actor_role", "audit_logs", "actor_role"),
    ("ix_trgm_audit_logs_payload", "audit_logs", "CAST(payload AS VARCHAR)"),
]


def upgrade() -> None:
    for name, table, expr in _INDEXES:
        op.execute(
            f"CREATE INDEX {name} ON {table} "
            f"USING gin (normalize_search_text({expr}) gin_trgm_ops)"
        )


def downgrade() -> None:
    for name, _table, _expr in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
