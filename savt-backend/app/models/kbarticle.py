from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KbArticle(Base):
    __tablename__ = "kbarticleies"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на категорию
    category_id: Mapped[int] = mapped_column(ForeignKey("kbcategories.id"), index=True)
    # заголовок
    title: Mapped[str] = mapped_column(String(500))
    # юрл идентификатор
    slug: Mapped[str] = mapped_column(String(500), unique=True)
    # содержимое
    content: Mapped[str] = mapped_column(Text)
    # номер версии
    version: Mapped[int] = mapped_column(Integer)
    # опубликована
    is_published: Mapped[bool] = mapped_column(Boolean)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата изменения
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<KbArticle id={self.id}>"