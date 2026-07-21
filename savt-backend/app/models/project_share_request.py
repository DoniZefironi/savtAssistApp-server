from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProjectShareRequest(Base):
    __tablename__ = "project_share_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # ссылка на проект
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # комментарий пользователя
    user_comment: Mapped[str | None] = mapped_column(Text)
    # статус заявки
    status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)
    # ответ администратора
    admin_response: Mapped[str | None] = mapped_column(Text)
    # кто обработал
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата обработки
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<ProjectShareRequest id={self.id} user_id={self.user_id} status={self.status}>"
