from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KbArticleTag(Base):
    __tablename__ = "kb_article_tags"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("kb_articles.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    def __repr__(self) -> str:
        return f"<KbArticleTag article_id={self.article_id} tag_id={self.tag_id}>"
