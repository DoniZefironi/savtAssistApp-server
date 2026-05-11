from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    # хеш токена
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # user-agent клиента
    user_agent: Mapped[str | None] = mapped_column(String(500))
    # ip-адрес
    ip_address: Mapped[str | None] = mapped_column(String(45))
    # время истечеения
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # время отзыва токена
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ссылка на заменивший токен
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )

    # дата создания
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    # время последнего использования
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<RefreshToken id={self.id} user_id={self.user_id} revoked={self.revoked_at is not None}>"