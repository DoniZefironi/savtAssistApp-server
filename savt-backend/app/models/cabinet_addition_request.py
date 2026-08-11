from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetAdditionRequest(Base):
    __tablename__ = "cabinet_addition_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Проект, в котором заявителю не хватает этого ШУ — заявитель уже должен
    # состоять в проекте (см. CabinetRequestService.create_addition). nullable —
    # заявки, поданные до перехода на проектную модель, приходят без него.
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # юрл фотографии заводской таблички
    photo_url: Mapped[str] = mapped_column(String(500))
    # комментарий пользователя
    user_comment: Mapped[str | None] = mapped_column(Text)
    # статус заявки
    status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)
    # ссылка на привязанный ШУ — при одобрении либо существующий шкаф без
    # проекта/не в том проекте (project_id у него выставляется на req.project_id),
    # либо только что заведённый через POST /admin/cabinets
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), nullable=True, index=True)
    # ответ администратора
    admin_response: Mapped[str | None] = mapped_column(Text)
    # кто обработал
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата обработки
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<CabinetAdditionRequest id={self.id}>"