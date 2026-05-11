from sqlalchemy import String, ForeignKey, BigInteger, Integer, DateTime, func
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessageAttachment(Base):
    __tablename__ = "message_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на сообщение
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # тип вложения
    attachment_type: Mapped[str] = mapped_column(String(20), index=True)
    # юрл файла
    file_url: Mapped[str] = mapped_column(String(500))
    # имя файла
    file_name: Mapped[str] = mapped_column(String(255))
    # размер байт
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    # миме тип
    mime_type: Mapped[str] = mapped_column(String(100))
    # длительность
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<MessageAttachment id={self.id} type={self.attachment_type} message_id={self.message_id}>"