from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # уведомления о сообщениях
    chat_messages: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # рекламные уведомления и рассылки администратора.
    # По умолчанию включены: переключатель до недавнего времени не проверялся
    # вовсе — рассылки уходили всем независимо от него. Оставить дефолт false,
    # начав его проверять, значило бы молча отключить рассылки почти всем.
    promotional: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # уведомление о гарантии
    warranty_expiring: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # уведомление о изменении статуса
    request_status_change: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    # --- временная пауза: глушит ВСЕ пуши, независимо от переключателей выше ---
    # Две колонки, а не одна с датой-«бесконечностью»: "тишина навсегда" — это
    # другое состояние, а не очень далёкая дата, и по сентинелу его пришлось бы
    # угадывать. Пауза не мешает копиться истории: пользователь просил не
    # беспокоить, а не выбрасывать уведомления.
    muted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    muted_indefinitely: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    def is_muted(self, now: datetime | None = None) -> bool:
        if self.muted_indefinitely:
            return True
        if self.muted_until is None:
            return False
        return self.muted_until > (now or datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<NotificationSettings user_id={self.user_id}>"