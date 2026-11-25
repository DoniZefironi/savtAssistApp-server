from pgvector.sqlalchemy import Vector
from sqlalchemy import String, and_, case, cast, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.embedding import EMBEDDING_DIM, Embedding
from app.models.faq_entry import FaqEntry
from app.models.kb_article_attachment import KbArticleAttachment
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
    distance = Embedding.embedding.op("<=>")(vector)
    part = Embedding.meta["kb_part"].astext
    published_article = and_(Embedding.source_id == KbArticle.id, KbArticle.is_published.is_(True))

    # Старые эмбеддинги статьи могут содержать закрытые вложения. Для них
    # до переиндексации используем только текст непосредственно из статьи.
    whole_records = (
        general_knowledge_query(
            case(
                (FaqEntry.id.is_not(None), func.concat(
                    "FAQ: ", func.left(FaqEntry.question, 500), "\n", func.left(FaqEntry.answer, 2500)
                )),
                else_=func.concat("База знаний: ", KbArticle.title, "\n", func.left(KbArticle.content, 2500)),
            ).label("material"),
            func.min(distance).label("distance"),
        )
        .outerjoin(FaqEntry, and_(
            Embedding.source_type == "faq", Embedding.source_id == FaqEntry.id,
            FaqEntry.is_published.is_(True),
        ))
        .outerjoin(KbArticle, and_(
            Embedding.source_type == "kb_article", published_article,
        ))
        .where(or_(
            FaqEntry.id.is_not(None),
            and_(part.is_(None), func.btrim(KbArticle.content) != ""),
        ))
        .group_by(Embedding.source_type, Embedding.source_id, FaqEntry.id, KbArticle.id)
    )
    public_attachment = select(KbArticleAttachment.id).where(
        KbArticleAttachment.article_id == KbArticle.id,
        cast(KbArticleAttachment.id, String) == Embedding.meta["attachment_id"].astext,
        KbArticleAttachment.file_url == Embedding.meta["file_url"].astext,
        KbArticleAttachment.requires_approval.is_(False),
    ).exists()
    article_chunks = (
        select(
            func.concat(
                case((part == "attachment", "Документ: "), else_="База знаний: "),
                KbArticle.title, "\n", Embedding.content,
            ).label("material"),
            distance.label("distance"),
        )
        .select_from(Embedding)
        .join(KbArticle, published_article)
        .where(Embedding.source_type == "kb_article", or_(
            and_(
                part == "body",
                func.btrim(KbArticle.content) != "",
                func.strpos(func.concat_ws("\n\n", KbArticle.title, KbArticle.content), Embedding.content) > 0,
            ),
            and_(part == "attachment", public_attachment),
        ))
    )
    stmt = union_all(whole_records, article_chunks).order_by("distance").limit(8)
    return (await session.execute(stmt)).scalars().all()
