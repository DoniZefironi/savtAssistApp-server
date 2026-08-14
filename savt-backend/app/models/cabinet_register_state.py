from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetRegisterState(Base):
    """Последнее известное значение КАЖДОГО регистра ШУ — по одной строке на
    (cabinet_id, address), перезаписывается на каждое входящее значение этого
    адреса. В отличие от CabinetTelemetryEvent (сырая история сообщений, для
    аудита) — здесь всегда актуальный снимок "что сейчас", без накопления:
    128 регистров = 128 строк на ШУ, а не растущий бесконечно журнал."""
    __tablename__ = "cabinet_register_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    cabinet_id: Mapped[int] = mapped_column(ForeignKey("cabinets.id", ondelete="CASCADE"), index=True)
    address: Mapped[int] = mapped_column(Integer)
    value: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("cabinet_id", "address", name="uq_cabinet_register_state_cabinet_address"),
    )

    def __repr__(self) -> str:
        return f"<CabinetRegisterState cabinet_id={self.cabinet_id} address={self.address} value={self.value}>"
