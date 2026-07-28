from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func, BigInteger, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    #  ссылка на ШУ — ровно одна из cabinet_id/project_id всегда заполнена (см. CHECK в БД)
    cabinet_id: Mapped[int | None] = mapped_column(
        ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # ссылка на проект (документация проекта в целом, не привязанная к конкретному ШУ)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # тип документа
    doc_type: Mapped[str] = mapped_column(String(50), index=True)
    # название
    title: Mapped[str] = mapped_column(String(255))
    # юрл файла
    file_url: Mapped[str] = mapped_column(String(500))
    # размер файла
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    # миме-тип
    mime_type: Mapped[str] = mapped_column(String(100))
    # требуется ли разрешение на доступ
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
    # номер версии
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата обработки
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} cabinet_id={self.cabinet_id} project_id={self.project_id} type={self.doc_type}>"