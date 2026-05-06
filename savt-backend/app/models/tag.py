from sqlalchemy import String 
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Название тега
    name: Mapped[str] = mapped_column(String(100), unique=True)

    def __repr__(self) -> str:
        return f"<Tag id={self.id}>"