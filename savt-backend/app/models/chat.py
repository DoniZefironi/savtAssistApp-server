from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Index

from app.database import Base


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # cabinet | support | notes
    chat_type: Mapped[str] = mapped_column(String(20), index=True)
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # --- Поля состояния бота (только для support-чата) ---
    # True = бот отвечает, False = оператор ведёт чат
    bot_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Пользователь запросил оператора, ждёт подключения
    operator_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Счётчик подряд идущих "нет" на вопрос "решилась проблема?"
    bot_no_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # open | resolved
    problem_status: Mapped[str] = mapped_column(
        String(20), default="open", server_default="open", index=True
    )
    # Отправлял ли бот follow-up после долгого молчания
    follow_up_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Время последнего сообщения от пользователя (для follow-up таймера)
    last_user_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Chat id={self.id} user_id={self.user_id} type={self.chat_type}>"

    __table_args__ = (
        # Один cabinet-чат на пару пользователь + шкаф
        Index(
            "uq_user_cabinet_chat",
            "user_id", "cabinet_id",
            unique=True,
            postgresql_where=text("chat_type = 'cabinet' AND cabinet_id IS NOT NULL"),
        ),
        # Один support-чат на пользователя
        Index(
            "uq_user_support_chat",
            "user_id",
            unique=True,
            postgresql_where=text("chat_type = 'support'"),
        ),
        # Один notes-чат на пользователя
        Index(
            "uq_user_notes_chat",
            "user_id",
            unique=True,
            postgresql_where=text("chat_type = 'notes'"),
        ),
        CheckConstraint(
            "problem_status IN ('open', 'resolved')",
            name="ck_chat_problem_status",
        ),
        CheckConstraint(
            "chat_type IN ('cabinet', 'support', 'notes')",
            name="ck_chat_type",
        ),
    )
