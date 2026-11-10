from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UploadLog(Base):
    """Журнал загрузок файлов пользователями: по нему считается суточная квота
    (app/services/upload_quota.py). Запись заводится до сохранения файла с заявленным размером
    (резерв), после сохранения размер уточняется, а если загрузка не удалась — запись удаляется.
    Ссылку на файл не хранит: иначе журнал считался бы местом, где файл ещё используется
    (см. app/services/file_references.py)."""
    __tablename__ = "upload_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_upload_log_user_created", "user_id", "created_at"),
    )
