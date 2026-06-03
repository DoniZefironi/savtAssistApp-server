from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("name", "scope", name="uq_tag_name_scope"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    # document | cabinet
    scope: Mapped[str] = mapped_column(String(20), default="document", index=True)