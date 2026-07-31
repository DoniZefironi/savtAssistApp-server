from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessengerLinkRequest(Base):
    """Ожидающее подтверждения 'рукопожатие' — пользователь ещё не доказал, что
    номер принадлежит ему, поэтому код пока не сгенерирован (см. auth_service._start_verification).

    Подтверждение идёт в два шага (см. messenger_webhook_service):
      1) бот получил /start <token> — запоминаем чат в external_chat_id и просим
         поделиться номером кнопкой request_contact;
      2) пришёл message.contact — Telegram сам ручается за номер; сверяем его с
         phone из этой заявки и только тогда привязываем связку и шлём код.
    Одного /start мало: он доказывает лишь владение Telegram-аккаунтом, но ничего
    не говорит о номере, который человек ввёл в форме."""
    __tablename__ = "messenger_link_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # одноразовый токен, зашитый в deep-link
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # номер телефона, который верифицируется
    phone: Mapped[str] = mapped_column(String(20), index=True)
    # registration | password_reset ("phone_change" больше не выпускается:
    # смена номера идёт через заявку с одобрением админа, см. PhoneChangeRequest)
    purpose: Mapped[str] = mapped_column(String(30))
    # Чат, приславший /start с этим токеном. Заполняется на первом шаге, чтобы на
    # втором (message.contact) найти заявку — токена в том апдейте уже не будет.
    external_chat_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # только telegram (Viber удалён)
    channel: Mapped[str] = mapped_column(String(20))
    # время истечения
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # время использования (бот получил /start с этим токеном)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<MessengerLinkRequest id={self.id} user_id={self.user_id} channel={self.channel}>"
