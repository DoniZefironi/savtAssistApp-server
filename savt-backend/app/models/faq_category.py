from sqlalchemy import String, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FaqCategory(Base):
    __tablename__ = "faq_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Родительская категория
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("faq_categories.id"))
    # Название
    name: Mapped[str] = mapped_column(String(200))
    # Порядок
    sort_order: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"<FaqCategory id={self.id}>"