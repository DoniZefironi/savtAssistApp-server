from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessageReaction(Base):
    __tablename__ = "message_reactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на сообщение
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # символ реакции
    emoji: Mapped[str] = mapped_column(String(20))
    # дата создания
    created_ad: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<MessageReaction id={self.id}>"