from sqlalchemy import CheckConstraint, String, ForeignKey, Integer, DateTime, func
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetPhoto(Base):
    """Фотография ШУ или проекта — ровно одна из двух привязок, как у Document.

    Имя таблицы историческое: фото проектов появились позже, а переименование
    задело бы все существующие запросы ради косметики."""
    __tablename__ = "cabinet_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на ШУ — ровно одна из cabinet_id/project_id заполнена (см. CHECK ниже)
    cabinet_id: Mapped[int | None] = mapped_column(
        ForeignKey("cabinets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # ссылка на проект (фотографии проекта в целом, не привязанные к конкретному ШУ)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # юрл изображения
    url: Mapped[str] = mapped_column(String(500))
    # подпись
    caption: Mapped[str | None] = mapped_column(String(500))
    # порядок отображения
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # дата создания
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    # Фактическое имя файла в подпапке «Фото» на NAS — заполняется один раз при
    # первом экспорте или при подхвате обратной синхронизацией. Дальше сверка
    # сверяется по этому имени, а не пересчитывает его из caption заново —
    # иначе смена подписи в приложении плодила бы на NAS второй файл вместо
    # переиспользования старого (см. Document.nas_filename — тот же принцип).
    nas_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<CabinetPhoto id={self.id} cabinet_id={self.cabinet_id} project_id={self.project_id}>"

    __table_args__ = (
        CheckConstraint(
            "(cabinet_id IS NOT NULL) != (project_id IS NOT NULL)",
            name="ck_photo_cabinet_or_project",
        ),
    )