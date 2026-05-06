from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя-владельца чата
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # тип чата
    chat_type: Mapped[str] = mapped_column(String(20))
    # ссылка на ШУ
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), index=True)
    # время последнего сообщения
    last_messaage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Chat id={self.id}>"