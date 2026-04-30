from datetime import datetime
from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PhoneVerificationCode(Base):
    __tablename__ = "phone_verification_codes"

    id: Mapped[int] = mapped_column(primary_key=True)

    phone: Mapped[str] = mapped_column(String(20), index=True)

    code_hash: Mapped[str] = mapped_column(String(255))

    purpose: Mapped[str] = mapped_column(String(30), index=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<PhoneVerificationCode id={self.id} phone={self.phone} purpose={self.purpose}>"