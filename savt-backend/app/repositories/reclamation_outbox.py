from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reclamation_bitrix_outbox import ReclamationBitrixOutbox


class ReclamationOutboxRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, reclamation_id: int, operation: str, payload: dict, error: str,
    ) -> ReclamationBitrixOutbox:
        row = ReclamationBitrixOutbox(
            reclamation_id=reclamation_id, operation=operation, payload=payload,
            attempts=1, last_error=error, last_attempted_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def has_pending_status_change(self, reclamation_id: int) -> bool:
        """Есть ли у этой рекламации неотправленная смена статуса. Пока такая
        висит, карточка в Bitrix заведомо отстала от нас, и принимать из неё
        статус по вебхуку нельзя — см. sync_reclamation_from_bitrix."""
        result = await self.session.execute(
            select(ReclamationBitrixOutbox.id)
            .where(
                ReclamationBitrixOutbox.reclamation_id == reclamation_id,
                ReclamationBitrixOutbox.operation == "status",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_pending(self, limit: int = 100) -> list[ReclamationBitrixOutbox]:
        result = await self.session.execute(
            select(ReclamationBitrixOutbox).order_by(ReclamationBitrixOutbox.created_at).limit(limit)
        )
        return list(result.scalars().all())

    def mark_failed_attempt(self, row: ReclamationBitrixOutbox, error: str) -> None:
        row.attempts += 1
        row.last_attempted_at = datetime.now(timezone.utc)
        row.last_error = error

    async def delete(self, row: ReclamationBitrixOutbox) -> None:
        await self.session.delete(row)
