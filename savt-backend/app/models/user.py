from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # номер телефона (только для пользователей)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    # логин (только для операторов/администраторов)
    login: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    # ФИО
    full_name: Mapped[str | None] = mapped_column(String(200))
    # Тип учётной записи (только для пользователя)
    user_type: Mapped[str | None] = mapped_column(String(20), comment="Значения: individual or organization")
    # Название организации (если выбрат тип организация)
    organization_name: Mapped[str | None] = mapped_column(String(255))
    # пароль
    hashed_password: Mapped[str] = mapped_column(String(255))
    # роль
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    # валидирован ли пароль
    is_phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # бан или актив?
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # для логов
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} phone={self.phone} role_id={self.role_id}>"