from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # fcm токен
    token: Mapped[str] = mapped_column(String(500), unique=True)
    # платформа
    platform: Mapped[str] = mapped_column(String(20), index=True)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # время последнего использования
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<DeviceToken id={self.id}>"
    
    __table_args__ = (
        CheckConstraint("platform IN ('ios', 'android')", name="ck_device_token_platform"),
    )