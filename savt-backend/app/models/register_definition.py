from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RegisterDefinition(Base):
    """Стандартная карта регистров — общая для всех ШУ. Что для конкретного ШУ
    переопределено/добавлено сверх неё, см. CabinetRegisterOverride."""
    __tablename__ = "register_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # адрес регистра (%MW-адрес на ПЛК)
    address: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    # человекочитаемое название
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<RegisterDefinition address={self.address} name={self.name!r}>"
