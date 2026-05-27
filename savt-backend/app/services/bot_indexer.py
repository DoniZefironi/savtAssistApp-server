import asyncio
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.embedding import EMBEDDING_DIM, Embedding
from app.models.faq_entry import FaqEntry
from app.models.kbarticle import KbArticle
from app.models.kb_article_attachment import KbArticleAttachment
from app.services import yandex_service

UPLOAD_ROOT = Path("/code/uploads")
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


# ── Чанкинг ──────────────────────────────────────────────────────────────────

def _chunks(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    result = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        result.append(text[start:end])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return result


# ── Парсинг файлов ────────────────────────────────────────────────────────────

def _parse_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _parse_docx(path: Path) -> str:
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        return ""


def _extract_text(file_url: str) -> str:
    """Извлекает текст из файла по URL вида /static/..."""
    relative = file_url.removeprefix("/static/")
    path = UPLOAD_ROOT / relative
    if not path.exists():
        return ""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix in (".docx", ".doc"):
        return _parse_docx(path)
    return ""


# ── Сохранение эмбеддингов ───────────────────────────────────────────────────

async def _upsert_chunks(
    session: AsyncSession,
    source_type: str,
    source_id: int,
    chunks: list[str],
    meta: dict,
) -> None:
    await session.execute(
        delete(Embedding).where(
            Embedding.source_type == source_type,
            Embedding.source_id == source_id,
        )
    )
    for i, chunk in enumerate(chunks):
        vector = await yandex_service.embed_document(chunk)
        session.add(Embedding(
            source_type=source_type,
            source_id=source_id,
            chunk_index=i,
            content=chunk,
            embedding=vector,
            meta=meta,
        ))
        # Yandex лимит: 10 запросов/сек → 0.12с между запросами = ~8/сек
        await asyncio.sleep(0.12)
    await session.flush()


# ── Публичные функции индексации ─────────────────────────────────────────────

async def index_faq_entry(session: AsyncSession, entry: FaqEntry) -> None:
    text = f"Вопрос: {entry.question}\nОтвет: {entry.answer}"
    await _upsert_chunks(session, "faq", entry.id, _chunks(text), {"title": entry.question})


async def index_kb_article(session: AsyncSession, article: KbArticle) -> None:
    parts = []
    if article.title:
        parts.append(article.title)
    if article.content:
        parts.append(article.content)

    attachments = (await session.execute(
        select(KbArticleAttachment).where(KbArticleAttachment.article_id == article.id)
    )).scalars().all()
    for att in attachments:
        parts.append(_extract_text(att.file_url))

    text = "\n\n".join(p for p in parts if p.strip())
    await _upsert_chunks(session, "kb_article", article.id, _chunks(text), {"title": article.title})


async def index_document(session: AsyncSession, doc: Document) -> None:
    text = _extract_text(doc.file_url)
    if not text.strip():
        text = doc.title or ""
    await _upsert_chunks(
        session, "document", doc.id, _chunks(text),
        {"title": doc.title, "cabinet_id": doc.cabinet_id},
    )


async def reindex_all(session: AsyncSession) -> dict:
    """Полная переиндексация всех источников. Возвращает статистику."""
    stats = {"faq": 0, "kb_article": 0, "document": 0}

    entries = (await session.execute(select(FaqEntry))).scalars().all()
    for e in entries:
        await index_faq_entry(session, e)
        stats["faq"] += 1

    articles = (await session.execute(select(KbArticle))).scalars().all()
    for a in articles:
        await index_kb_article(session, a)
        stats["kb_article"] += 1

    docs = (await session.execute(select(Document))).scalars().all()
    for d in docs:
        await index_document(session, d)
        stats["document"] += 1

    await session.commit()
    return stats
