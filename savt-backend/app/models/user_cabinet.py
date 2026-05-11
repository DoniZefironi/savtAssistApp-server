from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func, Text, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserCabinet(Base):
    __tablename__ = "user_cabinets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # ссылка на ШУ
    cabinet_id: Mapped[int] = mapped_column(
        ForeignKey("cabinets.id", ondelete="CASCADE"), index=True
    )
    # признак первичной привязки(true - первый, false - остальные)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # произвольное название
    custom_name: Mapped[str | None] = mapped_column(String(200))
    # личный комментарий
    custom_comment: Mapped[str | None] = mapped_column(Text)
    # дата привязки
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<UserCabinet id={self.id} user_id={self.user_id} cabinet_id={self.cabinet_id} primary={self.is_primary}>"
    
    __table_args__ = (
        UniqueConstraint("user_id", "cabinet_id", name="uq_user_cabinet"),
        Index(
            "uq_user_cabinet_primary",
            "cabinet_id",
            unique=True,
            postgresql_where=text("is_primary = true"),
        ),
    )