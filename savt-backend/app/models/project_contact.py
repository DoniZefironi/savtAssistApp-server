from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProjectContact(Base):
    """Контактное лицо заказчика из сделки Bitrix.

    Видно только операторам и администраторам: ФИО, телефоны и почты сотрудников
    заказчика — персональные данные, а доступ к проекту в приложении получают по
    QR-коду, то есть потенциально широкий круг людей.

    Набор синхронизируется с заменой: контакт, убранный из сделки в Bitrix,
    удаляется и здесь — иначе со временем в карточке повиснут уволившиеся."""
    __tablename__ = "project_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # ID контакта в Bitrix — по нему контакт обновляется на месте, а не дублируется
    bitrix_contact_id: Mapped[str] = mapped_column(String(20))
    full_name: Mapped[str | None] = mapped_column(String(300))
    # должность
    post: Mapped[str | None] = mapped_column(String(200))
    # У контакта в Bitrix телефоны и почты — множественные поля (рабочий, мобильный),
    # поэтому списками, а не одной строкой
    phones: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    emails: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # порядок из сделки: первым идёт основной контакт (CONTACT_ID)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("project_id", "bitrix_contact_id", name="uq_project_contact"),
    )

    def __repr__(self) -> str:
        return f"<ProjectContact id={self.id} project_id={self.project_id} name={self.full_name}>"
