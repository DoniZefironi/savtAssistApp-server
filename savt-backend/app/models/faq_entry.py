from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, func, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FaqEntry(Base):
    __tablename__ = "faq_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на категорию
    category_id: Mapped[int] = mapped_column(
        ForeignKey("faq_categories.id", ondelete="CASCADE"), index=True
    )
    # вопрос
    question: Mapped[str] = mapped_column(Text)
    # ответ
    answer: Mapped[str] = mapped_column(Text)
    # номер версии
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # опубликовано
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )

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
        return f"<FaqEntry id={self.id} category_id={self.category_id} published={self.is_published}>"