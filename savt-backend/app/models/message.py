from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на чат
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id"), index=True)
    # ссылка на отправител
    sender_id: Mapped[int] = mapped_column(ForeignKey("senders.id"), index=True)
    # текст сообщения
    text: Mapped[str | None] = mapped_column(Text)
    # ответ на сообщение
    reply_to_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    # прочитано
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    # время отправки
    created_ad: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # время редактирования
    edited_ad: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # время удаления
    deleted_ad: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Message id={self.id}>"