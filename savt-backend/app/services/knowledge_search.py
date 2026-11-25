from pgvector.sqlalchemy import Vector
from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.embedding import EMBEDDING_DIM, Embedding
from app.models.faq_entry import FaqEntry
from app.models.kbarticle import KbArticle
from app.services import yandex_service


async def knowledge_query_vector(query: str):
    return cast(await yandex_service.embed_query(query), Vector(EMBEDDING_DIM))


def general_knowledge_query(*columns):
    return select(*columns).select_from(Embedding).where(
        Embedding.source_type.in_(("faq", "kb_article")),
    )


async def retrieve_public_knowledge(session: AsyncSession, query: str) -> list[str]:
    vector = await knowledge_query_vector(query)
    # Эмбеддинги только ранжируют записи. В старом индексе текст статьи и вложений
    # смешан, в том числе после удаления файла, поэтому берём текст из самих записей.
    stmt = (
        general_knowledge_query(FaqEntry.question, FaqEntry.answer, KbArticle.title, KbArticle.content)
        .outerjoin(FaqEntry, and_(
            Embedding.source_type == "faq", Embedding.source_id == FaqEntry.id,
            FaqEntry.is_published.is_(True),
        ))
        .outerjoin(KbArticle, and_(
            Embedding.source_type == "kb_article", Embedding.source_id == KbArticle.id,
            KbArticle.is_published.is_(True),
        ))
        .where(or_(FaqEntry.id.is_not(None), func.btrim(KbArticle.content) != ""))
        .group_by(Embedding.source_type, Embedding.source_id, FaqEntry.id, KbArticle.id)
        .order_by(func.min(Embedding.embedding.op("<=>")(vector)))
        .limit(5)
    )
    materials = []
    for question, answer, title, content in (await session.execute(stmt)).all():
        if question is not None:
            materials.append(f"FAQ: {question[:500]}\n{answer[:2500]}")
        elif content and content.strip():
            materials.append(f"База знаний: {title}\n{content[:2500]}")
    return materials
