from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PasswordResetRequest(Base):
    """Заявка на сброс пароля — для пользователей без Telegram, для которых
    самостоятельный сброс (AuthService.password_reset_*, только channel=telegram)
    недоступен в принципе. Та же логика, что у PhoneChangeRequest: система не
    может подтвердить, что заявку подаёт владелец аккаунта — это делает вручную
    администратор вне приложения (например, звонком), одобрение здесь лишь
    фиксирует его решение."""
    __tablename__ = "password_reset_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # новый пароль, предложенный заявителем — применяется как есть при одобрении
    hashed_password: Mapped[str] = mapped_column(String(255))
    # обоснование от пользователя (потерял Telegram, сменил телефон и т.п.)
    user_comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)
    admin_response: Mapped[str | None] = mapped_column(Text)
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<PasswordResetRequest id={self.id} user_id={self.user_id} status={self.status}>"
