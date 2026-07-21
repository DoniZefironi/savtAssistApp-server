from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # название проекта
    name: Mapped[str] = mapped_column(String(200))
    # секретный кур-код
    unique_code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # зарезервировано под будущую вложенность (проект в проекте), пока не используется
    parent_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
