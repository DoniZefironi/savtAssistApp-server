from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetBlock(Base):
    __tablename__ = "cabinet_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # ссылка на ШУ
    cabinet_id: Mapped[int] = mapped_column(ForeignKey("cabinets.id"), index=True)
    # тип ограничения
    block_type: Mapped[str] = mapped_column(String(20))
    # причина
    reason: Mapped[str | None] = mapped_column(Text)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<CabinetBlock id={self.id}>"