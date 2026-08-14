from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RegisterDefinition(Base):
    """Стандартная карта регистров — общая для всех ШУ. Значение регистра —
    16-битное слово, каждый бит потенциально своя авария (не обязательно все
    биты именованы) — поэтому карта не address→name, а address+bit→name. Что
    для конкретного ШУ переопределено/добавлено сверх неё, см.
    CabinetRegisterOverride."""
    __tablename__ = "register_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # адрес регистра (%MW-адрес на ПЛК)
    address: Mapped[int] = mapped_column(Integer, index=True)
    # номер бита в 16-битном значении регистра, 0-15
    bit: Mapped[int] = mapped_column(Integer)
    # человекочитаемое название
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("address", "bit", name="uq_register_definition_address_bit"),
    )

    def __repr__(self) -> str:
        return f"<RegisterDefinition address={self.address} bit={self.bit} name={self.name!r}>"
