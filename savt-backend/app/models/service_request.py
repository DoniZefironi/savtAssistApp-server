from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, String, DateTime, ForeignKey, func, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ServiceRequest(Base):
    __tablename__ = "service_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # ссылка на ШУ — ровно одна из cabinet_id/project_id заполнена (см. CHECK
    # ниже), как у Document/CabinetPhoto/Chat
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), nullable=True, index=True)
    # ссылка на проект (заявка по проекту в целом, не привязанная к конкретному ШУ)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    # тип заявки (вид работ — не путать с гарантией, см. is_under_warranty)
    request_type: Mapped[str] = mapped_column(String(20), index=True)
    # Гарантийная ли заявка — снимок статуса гарантии ШУ/проекта на момент
    # СОЗДАНИЯ заявки, дальше никогда не пересчитывается: если администратор
    # позже продлит гарантию задним числом, уже созданная негарантийная заявка
    # должна остаться негарантийной (важно для биллинга — платно/бесплатно).
    is_under_warranty: Mapped[bool] = mapped_column(Boolean, index=True)
    # описание проблемы
    description: Mapped[str] = mapped_column(Text)
    # статус
    status: Mapped[str] = mapped_column(
        String(20), server_default="open", index=True
    )
    # дата создания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # дата обработки
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ID задачи в Bitrix24 (tasks.task.add), null если Bitrix не настроен или создание не удалось
    bitrix_task_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # ключ идемпотентности для оффлайн-очереди клиента (обычно tempId заявки).
    # Уникален в паре с user_id (см. индекс ниже) — повтор того же токена от
    # того же пользователя возвращает уже созданную заявку вместо дубля.
    client_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<ServiceRequest id={self.id} type={self.request_type} status={self.status}>"

    __table_args__ = (
        Index(
            "uq_service_requests_user_client_token",
            "user_id", "client_token",
            unique=True,
            postgresql_where=text("client_token IS NOT NULL"),
        ),
        CheckConstraint(
            "(cabinet_id IS NOT NULL) != (project_id IS NOT NULL)",
            name="ck_service_request_cabinet_or_project",
        ),
    )