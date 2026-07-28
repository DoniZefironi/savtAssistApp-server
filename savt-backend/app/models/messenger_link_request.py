from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessengerLinkRequest(Base):
    """Ожидающее подтверждения 'рукопожатие' — пользователь ещё не открывал
    deep-link на бота, поэтому код пока не сгенерирован (см. auth_service._start_verification)."""
    __tablename__ = "messenger_link_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # одноразовый токен, зашитый в deep-link
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # номер телефона, который верифицируется (при смене номера — new_phone, ещё не user.phone)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    # registration | password_reset | phone_change
    purpose: Mapped[str] = mapped_column(String(30))
    # telegram | viber
    channel: Mapped[str] = mapped_column(String(20))
    # время истечения
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # время использования (бот получил /start с этим токеном)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<MessengerLinkRequest id={self.id} user_id={self.user_id} channel={self.channel}>"
