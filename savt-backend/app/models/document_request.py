from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocumentRequest(Base):
    __tablename__ = "document_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # ссылка на ШУ
    cabinet_id: Mapped[int] = mapped_column(ForeignKey("cabinets.id"), index=True)
    # ссылка на документ
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), index=True)
    # тип запрашиваемого документа
    doc_type: Mapped[str] = mapped_column(String(50))
    # статус
    status: Mapped[str] = mapped_column(String(20))
    # сообщение пользователя
    user_message: Mapped[str | None] = mapped_column(Text)
    # ответ администратора
    admin_message: Mapped[str | None] = mapped_column(Text)
    # кто обработал
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата обработки
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<DocumentRequest id={self.id}>"