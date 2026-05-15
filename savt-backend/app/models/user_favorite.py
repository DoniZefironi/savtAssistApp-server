from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserFavorite(Base):
    __tablename__ = "user_favorites"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # тип сущности
    entity_type: Mapped[str] = mapped_column(String(30), index=True)
    # идентификатор сущности
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    # дата добавления
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<UserFavorite user_id={self.user_id} {self.entity_type}:{self.entity_id}>"

    __table_args__ = (
        UniqueConstraint("user_id", "entity_type", "entity_id", name="uq_user_favorite"),
        CheckConstraint(
            "entity_type IN ('document', 'kb_article')",
            name="ck_user_favorite_entity_type",
        ),
    )
