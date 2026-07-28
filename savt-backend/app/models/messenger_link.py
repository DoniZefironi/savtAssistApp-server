from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessengerLink(Base):
    __tablename__ = "messenger_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # telegram | viber
    channel: Mapped[str] = mapped_column(String(20))
    # chat_id (Telegram) или user id (Viber) — куда бот шлёт сообщения
    external_chat_id: Mapped[str] = mapped_column(String(64))
    # когда подтверждена связка (открыт deep-link, бот получил /start)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<MessengerLink id={self.id} user_id={self.user_id} channel={self.channel}>"

    __table_args__ = (
        UniqueConstraint("user_id", "channel", name="uq_messenger_links_user_channel"),
    )
