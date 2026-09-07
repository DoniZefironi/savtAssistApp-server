from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RegistrationRequest(Base):
    """Заявка на регистрацию — альтернатива самостоятельной регистрации через
    Telegram (см. AuthService.register_start): заявитель вводит номер телефона
    сам, без подтверждения через мессенджер. Апрув администратора здесь и
    заменяет то, что в обычной регистрации делает Telegram — подтверждает,
    что телефон и личность заявителя настоящие (см.
    RegistrationRequestService.approve)."""
    __tablename__ = "registration_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    user_type: Mapped[str] = mapped_column(String(20))
    organization_name: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    user_comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)
    admin_response: Mapped[str | None] = mapped_column(Text)
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    # проставляется при одобрении — созданный аккаунт, для трассировки из заявки
    created_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<RegistrationRequest id={self.id} phone={self.phone} status={self.status}>"
