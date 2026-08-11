from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetUserSettings(Base):
    """Личная персонализация ШУ (своё название/заметка) — витринные поля, без
    какой-либо семантики доступа. В проектной модели доступ к ШУ выводится из
    принадлежности к проекту (Cabinet.project_id + UserProject), а не хранится
    здесь — до перехода на неё это жило в UserCabinet вместе с правами."""
    __tablename__ = "cabinet_user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cabinet_id: Mapped[int] = mapped_column(ForeignKey("cabinets.id", ondelete="CASCADE"), index=True)
    custom_name: Mapped[str | None] = mapped_column(String(200))
    custom_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<CabinetUserSettings id={self.id} user_id={self.user_id} cabinet_id={self.cabinet_id}>"

    __table_args__ = (
        UniqueConstraint("user_id", "cabinet_id", name="uq_cabinet_user_settings"),
    )
