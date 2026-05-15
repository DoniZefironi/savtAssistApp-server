from datetime import datetime
from sqlalchemy import Boolean, BigInteger, ForeignKey, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KbArticleAttachment(Base):
    __tablename__ = "kb_article_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на статью
    article_id: Mapped[int] = mapped_column(
        ForeignKey("kb_articles.id", ondelete="CASCADE"), index=True
    )
    # юрл файла
    file_url: Mapped[str] = mapped_column(String(500))
    # размер файла
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    # тип файла (pdf, word, excel, photo, video)
    doc_type: Mapped[str] = mapped_column(String(50), index=True)
    # миме-тип
    mime_type: Mapped[str] = mapped_column(String(100))
    # название
    title: Mapped[str] = mapped_column(String(255))
    # требуется ли доступ
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
    # дата добавления
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<KbArticleAttachment id={self.id} article_id={self.article_id}>"
