from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # кто совершил действие
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # тип действия
    action: Mapped[str] = mapped_column(String(100))
    # тип сущности
    entity_type: Mapped[str] = mapped_column(String(50))
    # идентификатор сущности
    entity_id: Mapped[int | None] = mapped_column(Integer)
    # детали действия
    payload: Mapped[dict] = mapped_column(JSON)
    # время действия
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id}>"