from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PhoneChangeRequest(Base):
    """Заявка на смену номера телефона — подтверждает администратор вручную.

    Самообслуживание тут невозможно: SMS отключены полностью, а код доставляется
    в мессенджер по user_id, то есть в собственный Telegram/Viber заявителя. Такой
    код не доказывает про новый номер ровно ничего, поэтому раньше любой мог
    поставить своему аккаунту любой незанятый номер — занять чужой (владелец
    больше не смог бы зарегистрироваться) или показать сотрудникам чужой телефон
    в заявках и чатах. Владение номером теперь проверяет живой администратор
    вне системы, а здесь остаётся только след этой проверки."""
    __tablename__ = "phone_change_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # запрошенный номер в E.164 (нормализуется схемой, как и везде)
    new_phone: Mapped[str] = mapped_column(String(20), index=True)
    # номер на момент подачи — чтобы в истории было видно, что именно меняли
    old_phone: Mapped[str | None] = mapped_column(String(20))
    # обоснование от пользователя ((сменил оператора, потерял номер и т.п.)
    user_comment: Mapped[str | None] = mapped_column(Text)
    # статус заявки
    status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)
    # ответ администратора
    admin_response: Mapped[str | None] = mapped_column(Text)
    # кто обработал
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата обработки
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<PhoneChangeRequest id={self.id} user_id={self.user_id} status={self.status}>"
