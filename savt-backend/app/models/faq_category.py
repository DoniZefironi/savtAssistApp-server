from sqlalchemy import String, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FaqCategory(Base):
    __tablename__ = "faq_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    # родительская категория
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("faq_categories.id"), index=True
    )
    # название
    name: Mapped[str] = mapped_column(String(200))
    # порядок
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    def __repr__(self) -> str:
        return f"<FaqCategory id={self.id} name={self.name}>"