from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetRegisterOverride(Base):
    """Добавка/переопределение стандартной карты регистров (RegisterDefinition)
    для конкретного ШУ — либо новый адрес, которого нет в стандартной карте,
    либо другое название уже существующего адреса именно для этого ШУ.
    При расшифровке сначала смотрим сюда, и только если тут нет строки на
    нужный адрес — берём из RegisterDefinition."""
    __tablename__ = "cabinet_register_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    cabinet_id: Mapped[int] = mapped_column(ForeignKey("cabinets.id", ondelete="CASCADE"), index=True)
    address: Mapped[int] = mapped_column(Integer)
    # NULL — вся WORD одно значение; 0-15 — конкретный бит (см. RegisterDefinition)
    bit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("cabinet_id", "address", "bit", name="uq_cabinet_register_override_cabinet_address_bit"),
    )

    def __repr__(self) -> str:
        return f"<CabinetRegisterOverride cabinet_id={self.cabinet_id} address={self.address} bit={self.bit}>"
