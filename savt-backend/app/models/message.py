from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, func, String, Text, Index
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на чат
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    # ссылка на отправител
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # текст сообщения
    text: Mapped[str | None] = mapped_column(Text)
    # ключ идемпотентности для оффлайн-очереди клиента (обычно tempId отправки).
    # Уникален в паре с sender_id (см. индекс ниже) — повтор того же токена от
    # того же отправителя возвращает уже созданное сообщение вместо дубля.
    client_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ответ на сообщение
    reply_to_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), index=True
    )
    # прочитано
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
    # время отправки
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # время редактирования
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # время удаления
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Message id={self.id} chat_id={self.chat_id} sender_id={self.sender_id}>"
    
    __table_args__ = (
        Index("ix_messages_chat_created", "chat_id", "created_at"),
        Index(
            "uq_messages_sender_client_token",
            "sender_id", "client_token",
            unique=True,
            postgresql_where=sql_text("client_token IS NOT NULL"),
        ),
    )