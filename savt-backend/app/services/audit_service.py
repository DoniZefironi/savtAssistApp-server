from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.repositories.audit import AuditRepository
from app.schemas.audit import AuditLogOut
from app.schemas.pagination import PageOut, make_page


class AuditLogger:
    """Лёгкий хелпер — просто добавляет запись в сессию без commit."""

    def __init__(self, session: AsyncSession):
        self.session = session

    def log(
        self,
        action: str,
        entity_type: str,
        entity_id: int | None = None,
        actor_id: int | None = None,
        actor_role: str | None = None,
        payload: dict | None = None,
    ) -> None:
        self.session.add(AuditLog(
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
        ))


class AuditService:
    def __init__(self, session: AsyncSession):
        self.repo = AuditRepository(session)

    async def list_logs(
        self,
        actor_id: int | None,
        actor_role: str | None,
        action: str | None,
        entity_type: str | None,
        entity_id: int | None,
        search: str | None,
        search_in: str,
        date_from: datetime | None,
        date_to: datetime | None,
        sort_by: str,
        sort_order: str,
        page: int,
        size: int,
    ) -> PageOut[AuditLogOut]:
        rows, total = await self.repo.list_logs(
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            search=search,
            search_in=search_in,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=(page - 1) * size,
            limit=size,
        )
        items = [
            AuditLogOut(
                id=log.id,
                actor_id=log.actor_id,
                actor_role=log.actor_role,
                actor_name=actor_name,
                action=log.action,
                entity_type=log.entity_type,
                entity_id=log.entity_id,
                payload=log.payload,
                created_at=log.created_at,
            )
            for log, actor_name in rows
        ]
        return make_page(items, total, page, size)
