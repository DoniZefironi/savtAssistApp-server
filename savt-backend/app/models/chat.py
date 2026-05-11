from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя-владельца чата
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # тип чата
    chat_type: Mapped[str] = mapped_column(String(20), index=True)
    # ссылка на ШУ
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), index=True)
    # время последнего сообщения
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Chat id={self.id} user_id={self.user_id} type={self.chat_type}>"
    
    __table_args__ = (
        UniqueConstraint("user_id", "cabinet_id", name="uq_user_cabinet_chat"),
    )