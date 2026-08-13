from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RegisterDefinition(Base):
    """Стандартная карта регистров — общая для всех ШУ. Что для конкретного ШУ
    переопределено/добавлено сверх неё, см. CabinetRegisterOverride.

    Регистр может значить либо одно число целиком (bit=NULL — "Температура
    насоса"), либо быть битовой маской, где у каждого бита своё название
    (bit=0..15 — "Авария РКФ" на бите 0, "Затопление" на бите 1 и т.п., как в
    типовых ПЛК-таблицах "Неисправности"). Оба варианта могут сосуществовать
    в БД, per-address решает не адрес сам по себе, а то, какие строки на него
    заведены — если хоть одна с bit != NULL, весь адрес трактуется как маска."""
    __tablename__ = "register_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # адрес регистра (%MW-адрес на ПЛК)
    address: Mapped[int] = mapped_column(Integer, index=True)
    # NULL — вся 16-битная WORD одно значение; 0-15 — конкретный бит внутри неё
    bit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # человекочитаемое название
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    # NULL в UNIQUE не считается равным другому NULL (обычное поведение SQL) —
    # для (address, bit=NULL) уникальность отдельно проверяется в сервисе
    __table_args__ = (
        UniqueConstraint("address", "bit", name="uq_register_definition_address_bit"),
    )

    def __repr__(self) -> str:
        return f"<RegisterDefinition address={self.address} bit={self.bit} name={self.name!r}>"
