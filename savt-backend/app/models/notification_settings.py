from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # уведомления о сообщениях
    chat_messages: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # рекламные уведомления
    promotional: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # уведомление о гарантии
    warranty_expiring: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # уведомление о изменении статуса
    request_status_change: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    def __repr__(self) -> str:
        return f"<NotificationSettings user_id={self.user_id}>"