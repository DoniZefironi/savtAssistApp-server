from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatPinnedMessage(Base):
    __tablename__ = "chat_pinned_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_chat_pinned_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    pinned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<ChatPinnedMessage chat_id={self.chat_id} message_id={self.message_id}>"
