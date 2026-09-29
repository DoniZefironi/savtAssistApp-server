from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PinnedChat(Base):
    """Личное закрепление чата в списке — не путать с ChatPinnedMessage
    (закреплённые сообщения внутри чата). user_id здесь — не владелец чата
    (Chat.user_id), а тот, кто закрепил: сам пользователь у себя в списке,
    либо любой оператор/админ у себя, независимо друг от друга и от
    владельца — один и тот же чат может быть закреплён у одного человека и
    не закреплён у другого."""
    __tablename__ = "pinned_chats"
    __table_args__ = (
        UniqueConstraint("user_id", "chat_id", name="uq_pinned_chat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<PinnedChat user_id={self.user_id} chat_id={self.chat_id}>"
