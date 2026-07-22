from datetime import datetime
from sqlalchemy import Double, String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Cabinet(Base):
    __tablename__ = "cabinets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # секретный кур-код
    unique_code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # админская группировка по проекту (не влияет на владельцев ШУ)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # модель ШУ
    type: Mapped[str] = mapped_column(String(100), index=True)
    # номер объекта
    object_number: Mapped[str] = mapped_column(String(100))
    # описание
    description: Mapped[str | None] = mapped_column(Text)
    # начало гарантии (null - гарантии нет вообще)
    warranty_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # окончание гарантии (null - гарантии нет вообще)
    warranty_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # рабочее название
    admin_internal_name: Mapped[str | None] = mapped_column(String(200))
    # комментарий администратора
    admin_comment: Mapped[str | None] = mapped_column(Text)
    # назначение
    purpose: Mapped[str | None] = mapped_column(String(200))
    # геолокация ШУ
    latitude: Mapped[float | None] = mapped_column(Double)
    longitude: Mapped[float | None] = mapped_column(Double)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    # дата обработки
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    # soft-delete: если не NULL - ШУ считается удалённым.
    # unique_code при этом остаётся занятым навсегда - новый ШУ не может
    # получить код уже удалённого (см. CabinetRepository.find_by_code).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<Cabinet id={self.id} object_number={self.object_number}>"