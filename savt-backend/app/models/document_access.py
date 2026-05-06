from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocumentAccess(Base):
    __tablename__ = "document_accesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на документ
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    # кто выдал доступ
    granted_by_admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # дата выдачи
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<DocumentAccess id={self.id}>"