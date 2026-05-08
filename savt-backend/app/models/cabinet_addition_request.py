from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetAdditionRequest(Base):
    __tablename__ = "cabinet_addition_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # юрл фотографии заводской таблички
    photo_url: Mapped[str] = mapped_column(String(500))
    # комментарий пользователя
    user_comment: Mapped[str | None] = mapped_column(Text)
    # статус заявки
    status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)
    # ссылка на привязанный ШУ
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), nullable=True, index=True)
    # ответ администратора
    admin_response: Mapped[str | None] = mapped_column(Text)
    # кто обработал
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата обработки
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<CabinetAdditionRequest id={self.id}>"