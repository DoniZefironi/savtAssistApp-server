from datetime import datetime
from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PromoMessage(Base):
    """Заготовка рекламного уведомления — раньше жили в файле на сервере
    (promo_messages.json, правился вручную), теперь заводятся/редактируются
    из админки (см. app/routers/notifications.py, PromoMessage*)."""
    __tablename__ = "promo_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(String(1000))
    # произвольные доп. поля для клиента (например deeplink на экран приложения)
    data: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<PromoMessage id={self.id} title={self.title!r}>"
