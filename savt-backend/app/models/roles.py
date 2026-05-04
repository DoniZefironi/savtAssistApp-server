from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    # название роли
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    def __repr__(self) -> str:
        return f"<Role id={self.id} name={self.name}>"