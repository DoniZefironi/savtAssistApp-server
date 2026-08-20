from datetime import datetime

from sqlalchemy import cast, func, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User
from app.utils.db import fuzzy_condition

_SORTABLE = {
    "created_at": AuditLog.created_at,
    "action": AuditLog.action,
    "entity_type": AuditLog.entity_type,
    "actor_role": AuditLog.actor_role,
    "actor_id": AuditLog.actor_id,
}

# Как и в остальных репозиториях — fuzzy_condition (normalize_search_text + pg_trgm)
# вместо голого ILIKE: тот не задействует индекс при ведущем "%" и не прощает
# опечатки/регистр/разделители. payload — тем же приведением к тексту (CAST AS
# VARCHAR), что и в GIN-индексе для audit_logs (см. миграцию a7c4e91f2b83)
_SEARCHABLE = {
    "action": lambda s: fuzzy_condition(s, AuditLog.action),
    "entity_type": lambda s: fuzzy_condition(s, AuditLog.entity_type),
    "actor_name": lambda s: fuzzy_condition(s, User.full_name),
    "payload": lambda s: fuzzy_condition(s, cast(AuditLog.payload, String)),
    "all": lambda s: fuzzy_condition(
        s, AuditLog.action, AuditLog.entity_type, AuditLog.actor_role, User.full_name, cast(AuditLog.payload, String),
    ),
}


class AuditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_logs(
        self,
        actor_id: int | None = None,
        actor_role: str | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_types: list[str] | None = None,
        entity_id: int | None = None,
        search: str | None = None,
        search_in: str = "all",
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[tuple[AuditLog, str | None]], int]:
        base = (
            select(AuditLog, User.full_name.label("actor_name"))
            .outerjoin(User, AuditLog.actor_id == User.id)
        )

        conditions = []
        if actor_id is not None:
            conditions.append(AuditLog.actor_id == actor_id)
        if actor_role is not None:
            conditions.append(AuditLog.actor_role == actor_role)
        if action is not None:
            conditions.append(AuditLog.action == action)
        if entity_type is not None:
            conditions.append(AuditLog.entity_type == entity_type)
        # Принудительное сужение по роли вызывающего (см. admin_audit.py) —
        # не то же самое, что entity_type: это ограничение видимости, а не фильтр пользователя
        if entity_types is not None:
            conditions.append(AuditLog.entity_type.in_(entity_types))
        if entity_id is not None:
            conditions.append(AuditLog.entity_id == entity_id)
        if date_from is not None:
            conditions.append(AuditLog.created_at >= date_from)
        if date_to is not None:
            conditions.append(AuditLog.created_at <= date_to)
        if search and search_in in _SEARCHABLE:
            conditions.append(_SEARCHABLE[search_in](search))

        if conditions:
            base = base.where(*conditions)

        count_stmt = select(func.count(AuditLog.id)).outerjoin(User, AuditLog.actor_id == User.id)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        col = _SORTABLE.get(sort_by, AuditLog.created_at)
        order = col.desc() if sort_order == "desc" else col.asc()
        stmt = base.order_by(order).offset(offset).limit(limit)

        rows = (await self.session.execute(stmt)).all()
        return [(log, actor_name) for log, actor_name in rows], total
