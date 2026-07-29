from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # название проекта
    name: Mapped[str] = mapped_column(String(200))
    # секретный кур-код — для проектов из Bitrix это Fernet-токен (номер проекта,
    # зашифрованный обратимо, см. app/services/project_code_service.py), поэтому
    # шире, чем у Cabinet.unique_code
    unique_code: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    # открытый номер проекта из Bitrix (например "26_138") — для поиска "уже есть
    # ли проект с таким номером" при повторных вызовах вебхука. unique_code для
    # этого не годится: шифруется со случайным IV, при каждом вызове получается
    # новая строка даже для одного и того же номера. null — проекты, заведённые
    # не из Bitrix (вручную через админку)
    production_number: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    # вложенность (проект в проекте) — например, отдельная партия отгрузки внутри
    # одного производственного проекта
    parent_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # санитизированное имя папки на NAS, реально использованное при последнем
    # создании/переименовании — по нему определяем, разошлось ли name с папкой на диске
    folder_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # когда последний раз успешно прогонялась синхронизация папки с NAS
    folder_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    # дата обработки
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    # soft-delete: если не NULL - проект считается удалённым.
    # unique_code при этом остаётся занятым навсегда, как и у Cabinet.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<Project id={self.id} name={self.name}>"
