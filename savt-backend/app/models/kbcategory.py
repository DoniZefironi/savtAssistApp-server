from sqlalchemy import String, ForeignKey, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KbCategory(Base):
    __tablename__ = "kbcategories"

    id: Mapped[int] = mapped_column(primary_key=True)
    # родительская категория
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("kbcategories,id"))
    # название
    name: Mapped[str] = mapped_column(String(200))
    # юрл идентификатор
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    # описание
    description: Mapped[str | None] = mapped_column(Text)
    # порядок отображения
    sort_order: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"<KbCategory id={self.id}>"