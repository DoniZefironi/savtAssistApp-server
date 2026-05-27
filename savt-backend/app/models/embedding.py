from datetime import datetime
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

EMBEDDING_DIM = 256


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # faq | kb_article | document
    source_type: Mapped[str] = mapped_column(String(20), index=True)
    # id записи в соответствующей таблице
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    # порядковый номер куска внутри документа
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    # сам текстовый кусок (для отдачи в GPT как контекст)
    content: Mapped[str] = mapped_column(Text)
    # вектор
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIM))
    # заголовок источника, cabinet_id и прочее
    meta: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Embedding {self.source_type}:{self.source_id} chunk={self.chunk_index}>"
