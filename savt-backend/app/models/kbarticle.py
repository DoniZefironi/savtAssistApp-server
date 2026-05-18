from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KbArticle(Base):
    __tablename__ = "kb_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на категорию
    category_id: Mapped[int] = mapped_column(
        ForeignKey("kb_categories.id", ondelete="CASCADE"), index=True
    )
    # заголовок
    title: Mapped[str] = mapped_column(String(500), index=True)
    # юрл идентификатор
    slug: Mapped[str] = mapped_column(String(500), unique=True)
    # краткое описание (опционально)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # номер версии
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # опубликована
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата изменения
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<KbArticle id={self.id} title={self.title[:30]} published={self.is_published}>"