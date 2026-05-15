from sqlalchemy import Boolean, ForeignKey, Integer, String, DateTime, func, Text
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)

    # иб пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # тип уведа
    type: Mapped[str] = mapped_column(String(20), comment="Значения: chat_message or warranty_expiring or promotional or request_status")
    # 
    title: Mapped[str | None] = mapped_column(String(255))
    # 
    body: Mapped[str | None] = mapped_column(Text)
    # 
    data: Mapped[dict | None] = mapped_column(JSONB)
    # 
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # время создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Notification user_id={self.user_id}>"