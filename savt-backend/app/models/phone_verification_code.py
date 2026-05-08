from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PhoneVerificationCode(Base):
    __tablename__ = "phone_verification_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # номер телефона
    phone: Mapped[str] = mapped_column(String(20), index=True)
    # хеш смс-кода
    code_hash: Mapped[str] = mapped_column(String(255))
    # Назвачение кода (registration or password_reset)
    purpose: Mapped[str] = mapped_column(String(30), index=True)
    # кол-во попыток ввода
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # лимит попыток
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    # время истечения
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # время использования
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # дата создания
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<PhoneVerificationCode id={self.id} phone={self.phone} purpose={self.purpose}>"