from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserCabinet(Base):
    """Прямое владение ШУ, в обход проекта — для ШУ, добавленного пользователем
    отдельно по своему QR (см. Cabinet.unique_code), а не через UserProject.
    Доступ к ШУ даёт любое из двух: UserProject на его project_id ИЛИ
    UserCabinet напрямую (см. CabinetRepository.user_has_access). Если ШУ
    позже попадает в проект, который пользователь тоже добавляет — эта строка
    удаляется (доступ дальше идёт через UserProject, см. UserProjectService)."""
    __tablename__ = "user_cabinets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    cabinet_id: Mapped[int] = mapped_column(
        ForeignKey("cabinets.id", ondelete="CASCADE"), index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<UserCabinet id={self.id} user_id={self.user_id} cabinet_id={self.cabinet_id}>"

    __table_args__ = (
        UniqueConstraint("user_id", "cabinet_id", name="uq_user_cabinet"),
    )
