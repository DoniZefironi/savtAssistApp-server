from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ServiceRequest(Base):
    __tablename__ = "service_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # ссылка на ШУ
    cabinet_id: Mapped[int] = mapped_column(ForeignKey("cabinets.id"), index=True)
    # тип заявки
    request_type: Mapped[str] = mapped_column(String(20))
    # описание проблемы
    description: Mapped[str] = mapped_column(Text)
    # статус
    status: Mapped[str] = mapped_column(String(20))
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата обработки
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<ServiceRequest id={self.id}>"