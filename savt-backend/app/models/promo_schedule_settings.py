from datetime import datetime
from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PromoScheduleSettings(Base):
    """Настройки автоматической рассылки рекламных уведомлений — singleton
    (всегда ровно одна строка, id=1). Управляется из админки, а не .env —
    раньше час рассылки читался один раз при старте процесса (PROMO_AUTO_SEND_HOUR),
    поменять его можно было только через рестарт сервера. См.
    app/services/promo_service.py."""
    __tablename__ = "promo_schedule_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # раз в сколько дней слать (1 — каждый день)
    interval_days: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # час отправки, 0-23, по UTC — так же, как и остальные cron-задачи в main.py
    send_hour: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    # None/пусто — выбор случайной заготовки среди ВСЕХ в файле; иначе — только
    # среди перечисленных id (см. PromoMessageOut.id)
    message_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # не повторять ту же заготовку два раза подряд в автоматической рассылке
    last_sent_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<PromoScheduleSettings enabled={self.enabled} interval_days={self.interval_days}>"
