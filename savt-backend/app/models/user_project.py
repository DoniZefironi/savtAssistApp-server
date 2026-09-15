from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, func, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserProject(Base):
    __tablename__ = "user_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на пользователя
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # ссылка на проект
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # признак первичной привязки (true - первый, false - остальные)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # дата привязки
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # закреп проекта наверх списка (GET /projects), см. POST/DELETE
    # /projects/{id}/pin — живёт прямо на членстве, а не в user_favorites:
    # закреп имеет смысл только для проекта, к которому есть доступ, и должен
    # исчезать сам, когда пользователь покидает проект (строка UserProject
    # удаляется целиком — отдельно чистить закреп не нужно)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<UserProject id={self.id} user_id={self.user_id} project_id={self.project_id} primary={self.is_primary}>"

    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_user_project"),
        Index(
            "uq_user_project_primary",
            "project_id",
            unique=True,
            postgresql_where=text("is_primary = true"),
        ),
    )
