from sqlalchemy import ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatUserSettings(Base):
    __tablename__ = "chat_user_settings"
    __table_args__ = (
        # один глобальный профиль на пользователя (chat_id IS NULL)
        Index(
            "uq_user_global_settings", "user_id",
            unique=True,
            postgresql_where=text("chat_id IS NULL"),
        ),
        # один per-chat профиль на пару (user_id, chat_id)
        Index(
            "uq_user_chat_settings", "user_id", "chat_id",
            unique=True,
            postgresql_where=text("chat_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # NULL = глобальные настройки пользователя; NOT NULL = override для конкретного чата
    chat_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    own_bubble_color: Mapped[str | None] = mapped_column(String(7))
    other_bubble_color: Mapped[str | None] = mapped_column(String(7))
    bot_bubble_color: Mapped[str | None] = mapped_column(String(7))
    own_text_color: Mapped[str | None] = mapped_column(String(7))
    other_text_color: Mapped[str | None] = mapped_column(String(7))
    bot_text_color: Mapped[str | None] = mapped_column(String(7))
    nick_color: Mapped[str | None] = mapped_column(String(7))
    font_size: Mapped[int | None] = mapped_column(SmallInteger)
    wallpaper_url: Mapped[str | None] = mapped_column(String(500))
