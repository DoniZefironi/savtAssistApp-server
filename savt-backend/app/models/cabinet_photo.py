from sqlalchemy import String, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetPhoto(Base):
    __tablename__ = "cabinet_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ссылка на ШУ
    cabinet_id: Mapped[int] = mapped_column(ForeignKey("cabinets.id"), index=True)
    # юрл изображения
    url: Mapped[str] = mapped_column(String(500))
    # подпись
    caption: Mapped[str | None] = mapped_column(String(500))
    # порядок отображения
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")

    def __repr__(self) -> str:
        return f"<CabinetPhoto id={self.id}>"