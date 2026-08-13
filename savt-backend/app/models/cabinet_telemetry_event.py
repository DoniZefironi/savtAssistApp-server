from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetTelemetryEvent(Base):
    """Одно входящее сообщение с контроллера ШУ — сырые пары регистр:значение,
    как прислал C#-прокси. Расшифровка (RegisterDefinition/CabinetRegisterOverride)
    делается на чтение, не здесь — поэтому правки карты регистров задним числом
    сразу применяются ко всей уже накопленной истории."""
    __tablename__ = "cabinet_telemetry_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    cabinet_id: Mapped[int] = mapped_column(ForeignKey("cabinets.id", ondelete="CASCADE"), index=True)
    # время фактического чтения на ПЛК (из вебхука) или, если не прислали, время получения
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # {"500": 9, "501": 5732, ...} — адрес регистра -> значение, как пришло
    raw_payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<CabinetTelemetryEvent id={self.id} cabinet_id={self.cabinet_id}>"
