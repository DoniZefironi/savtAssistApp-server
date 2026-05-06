from datetime import datetime
from sqlalchemy import String, DateTime, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Cabinet(Base):
    __tablename__ = "cabinets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # секретный кур-код
    unique_code: Mapped[str] = mapped_column(String(100), unique=True)
    # модель ШУ
    type: Mapped[str] = mapped_column(String(100))
    # номер объекта
    object_number: Mapped[str] = mapped_column(String(100))
    # описание 
    description: Mapped[str | None] = mapped_column(Text)
    # начало гарантии
    warranty_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # окончание гарантии
    warranty_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # рабочее название
    admin_internal_name: Mapped[str | None] = mapped_column(String(200))
    # комментарий администратора
    admim_comment: Mapped[str | None] = mapped_column(Text)

    # дата создания
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id}>"