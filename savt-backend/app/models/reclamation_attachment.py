from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReclamationAttachment(Base):
    """Файл, приложенный к рекламации (фото/видео/лог/документ, см. п.4 ТЗ).
    Загрузка — через общий POST /upload/attachment (как и везде в приложении),
    здесь сохраняется только ссылка. Передача этих же файлов в саму карточку
    Битрикс24 (реальным вложением, не ссылкой) — отдельная задача следующей
    фазы, пока не реализована."""
    __tablename__ = "reclamation_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    reclamation_id: Mapped[int] = mapped_column(
        ForeignKey("reclamations.id", ondelete="CASCADE"), index=True
    )
    file_url: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<ReclamationAttachment id={self.id} reclamation_id={self.reclamation_id}>"
