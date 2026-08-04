from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PendingRegistration(Base):
    """Незавершённая регистрация: данные формы, пока не известен номер телефона.

    Номер аккаунта берётся из контакта Telegram, а не из формы, — поэтому в момент
    отправки формы его ещё нет, а создать User без телефона не даёт constraint
    ck_users_phone_or_login. Данные ждут здесь.

    Жизненный цикл:
      1) POST /auth/register/start — строка создана, клиент получает token и deep-link;
      2) бот получил /start <token> — запоминаем external_chat_id, просим контакт;
      3) пришёл контакт — номер известен и подтверждён Telegram: создаём User,
         проставляем user_id и шлём код;
      4) POST /auth/register/complete с этим же token и кодом — consumed_at.
    """
    __tablename__ = "pending_registrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # выдаётся клиенту и зашивается в deep-link на бота
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # данные формы регистрации
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200))
    user_type: Mapped[str | None] = mapped_column(String(20))
    organization_name: Mapped[str | None] = mapped_column(String(255))
    # необязательный рабочий номер из формы — НЕ подтверждается и на вход не влияет
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    # чат, приславший /start: по нему находим заявку, когда придёт контакт
    # (в апдейте с контактом токена уже нет)
    external_chat_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # появляется после подтверждения номера: пользователь создан, ждём код
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Почему бот отказал на шаге подтверждения номера. Отказ виден только в
    # Telegram, а приложение стоит на экране ввода кода — без этого поля оно не
    # может отличить "ещё не нажал кнопку" от "отказано" и ждёт код вечно.
    # Значения: foreign_contact | bad_phone | phone_already_registered |
    #           telegram_already_linked
    failed_reason: Mapped[str | None] = mapped_column(String(40))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<PendingRegistration id={self.id} user_id={self.user_id}>"
