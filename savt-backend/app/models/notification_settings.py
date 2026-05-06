from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    user_id: Mapped[int] = mapped_column(Integer,ForeignKey("users.id"), primary_key=True)
    # уведомления о сообщениях
    chat_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    # рекламные уведомления
    promotional: Mapped[bool] = mapped_column(Boolean, default=False)
    # уведомление о гарантии
    warranty_expiring: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:
        return f"<NotificationSettings id={self.id}>"