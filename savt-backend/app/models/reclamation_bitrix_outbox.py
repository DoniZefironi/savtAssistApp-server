from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReclamationBitrixOutbox(Base):
    """Недоставленная попытка синхронизации рекламации с Bitrix24 (п.8 ТЗ) —
    строка заводится, когда create/status/assignee-вызов в bitrix_service
    падает с ошибкой, и удаляется при успешном повторе. Разбирает фоновая
    задача retry_bitrix_outbox (см. reclamation_service.py), раз в 15 минут,
    как и sync_statuses_from_bitrix у сервисных заявок.

    operation: create — заведение карточки, status — перевод стадии, выведенной
    из нашего статуса, assignee — назначение ответственного, stage — ручной
    перевод между "Новая рекламация" и "На рассмотрении" (обе = наш review,
    статусом их не различить, см. ReclamationService._check_stage_change).

    payload — то, с чем именно вызывать bitrix_service при повторе (не
    текущее состояние Reclamation на момент повтора, а снимок на момент
    сбоя) — так гарантированно повторяем именно ту операцию, что не удалась,
    а не что-то более позднее вперемешку."""
    __tablename__ = "reclamation_bitrix_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    reclamation_id: Mapped[int] = mapped_column(
        ForeignKey("reclamations.id", ondelete="CASCADE"), index=True
    )
    # create (создание элемента) | status (смена стадии) | assignee (ответственный)
    operation: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ReclamationBitrixOutbox id={self.id} reclamation_id={self.reclamation_id} "
            f"operation={self.operation} attempts={self.attempts}>"
        )

    __table_args__ = (
        CheckConstraint(
            "operation IN ('create', 'status', 'assignee', 'stage', 'deadline')",
            name="ck_reclamation_bitrix_outbox_operation",
        ),
    )
