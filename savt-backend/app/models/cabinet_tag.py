from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CabinetTag(Base):
    __tablename__ = "cabinet_tags"

    cabinet_id: Mapped[int] = mapped_column(
        ForeignKey("cabinets.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
