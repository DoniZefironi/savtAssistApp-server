import asyncio
import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.embedding import EMBEDDING_DIM, Embedding
from app.models.faq_entry import FaqEntry
from app.models.kbarticle import KbArticle
from app.models.kb_article_attachment import KbArticleAttachment
from app.services import yandex_service

logger = logging.getLogger(__name__)

UPLOAD_ROOT = Path("/code/uploads")
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100



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



async def _parse_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
    except Exception:
        logger.exception("Не удалось открыть PDF: %s", path)
        return ""

    parts: list[str] = []
    for page_num, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(text)
            continue
        # Страница без текстового слоя — похоже на скан. Достаём встроенные
        # изображения страницы напрямую через pypdf (без poppler/pdf2image)
        # и распознаём их через Yandex Vision OCR.
        try:
            images = list(page.images)
        except Exception:
            images = []
        for img in images:
            try:
                ocr_text = await yandex_service.ocr_image(img.data)
                if ocr_text.strip():
                    parts.append(ocr_text)
            except Exception:
                logger.exception(
                    "OCR не удался для страницы %d файла %s", page_num + 1, path
                )
            await asyncio.sleep(0.2)
    return "\n\n".join(parts)


def _parse_docx(path: Path) -> str:
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument(str(path))
        parts = [p.text for p in doc.paragraphs]
        # Технические характеристики в таких документах часто оформлены
        # таблицами — doc.paragraphs их не видит, обходим отдельно.
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    except Exception:
        logger.exception("Не удалось разобрать Word-документ: %s", path)
        return ""


def _parse_excel(path: Path) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        parts = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception:
        logger.exception("Не удалось разобрать Excel-файл: %s", path)
        return ""


async def _ocr_image_file(path: Path) -> str:
    try:
        return await yandex_service.ocr_image(path.read_bytes())
    except Exception:
        logger.exception("OCR не удался для изображения: %s", path)
        return ""


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

# Старые бинарные форматы (Word/Excel 97-2003) современными библиотеками не
# читаются в принципе — нужна конвертация в .docx/.xlsx, а не парсинг.
_UNSUPPORTED_LEGACY_FORMATS = {
    ".doc": "Word 97-2003 (.doc) — пересохраните файл как .docx",
    ".xls": "Excel 97-2003 (.xls) — пересохраните файл как .xlsx",
}


async def _extract_text(file_url: str) -> str:
    relative = file_url.removeprefix("/static/")
    path = UPLOAD_ROOT / relative
    if not path.exists():
        logger.warning("Файл для индексации не найден на диске: %s", path)
        return ""

    suffix = path.suffix.lower()
    if suffix in _UNSUPPORTED_LEGACY_FORMATS:
        logger.warning(
            "Формат не поддерживается извлечением текста (%s): %s",
            _UNSUPPORTED_LEGACY_FORMATS[suffix], path,
        )
        return ""

    if suffix == ".pdf":
        text = await _parse_pdf(path)
    elif suffix == ".docx":
        text = _parse_docx(path)
    elif suffix == ".xlsx":
        text = _parse_excel(path)
    elif suffix in _IMAGE_SUFFIXES:
        text = await _ocr_image_file(path)
    else:
        logger.info("Формат %s не поддерживается для извлечения текста: %s", suffix, path)
        return ""

    if not text.strip():
        logger.warning("Извлечённый текст пуст (файл без текстового слоя?): %s", path)
    return text



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
        parts.append(await _extract_text(att.file_url))

    text = "\n\n".join(p for p in parts if p.strip())
    await _upsert_chunks(session, "kb_article", article.id, _chunks(text), {"title": article.title})


async def index_document(session: AsyncSession, doc: Document) -> None:
    text = await _extract_text(doc.file_url)
    if not text.strip():
        text = doc.title or ""
    await _upsert_chunks(
        session, "document", doc.id, _chunks(text),
        {"title": doc.title, "cabinet_id": doc.cabinet_id},
    )


async def reindex_all(session: AsyncSession, force: bool = False) -> dict:
    """Индексирует только ещё не проиндексированные записи.
    force=True — переиндексирует всё (старое поведение).
    """
    stats = {"faq": 0, "kb_article": 0, "document": 0, "skipped": 0}

    if not force:
        rows = (await session.execute(
            select(Embedding.source_type, Embedding.source_id).distinct()
        )).all()
        already = {(r[0], r[1]) for r in rows}
    else:
        already = set()

    entries = (await session.execute(select(FaqEntry))).scalars().all()
    for e in entries:
        if ("faq", e.id) in already:
            stats["skipped"] += 1
            continue
        await index_faq_entry(session, e)
        stats["faq"] += 1

    articles = (await session.execute(select(KbArticle))).scalars().all()
    for a in articles:
        if ("kb_article", a.id) in already:
            stats["skipped"] += 1
            continue
        await index_kb_article(session, a)
        stats["kb_article"] += 1

    docs = (await session.execute(select(Document))).scalars().all()
    for d in docs:
        if ("document", d.id) in already:
            stats["skipped"] += 1
            continue
        await index_document(session, d)
        stats["document"] += 1

    await session.commit()
    return stats


_MODEL_BY_SOURCE_TYPE = {
    "faq": FaqEntry,
    "kb_article": KbArticle,
    "document": Document,
}


async def prune_orphaned(session: AsyncSession) -> dict:
    """Удаляет embeddings, чей источник (FAQ/статья КБ/документ) больше не
    существует. Основной сценарий — удаление категории каскадно сносит её
    статьи/вопросы на уровне БД (ondelete=CASCADE), в обход сервисного
    delete(), который обычно чистит embeddings сам."""
    stats = {"faq": 0, "kb_article": 0, "document": 0}

    rows = (await session.execute(
        select(Embedding.source_type, Embedding.source_id).distinct()
    )).all()
    ids_by_type: dict[str, set[int]] = {}
    for source_type, source_id in rows:
        ids_by_type.setdefault(source_type, set()).add(source_id)

    for source_type, ids in ids_by_type.items():
        model = _MODEL_BY_SOURCE_TYPE.get(source_type)
        if model is None:
            continue
        existing_ids = set((await session.execute(
            select(model.id).where(model.id.in_(ids))
        )).scalars().all())
        orphan_ids = ids - existing_ids
        if not orphan_ids:
            continue
        await session.execute(
            delete(Embedding).where(
                Embedding.source_type == source_type,
                Embedding.source_id.in_(orphan_ids),
            )
        )
        stats[source_type] = len(orphan_ids)
        logger.info("Удалено %d осиротевших embeddings типа %s", len(orphan_ids), source_type)

    await session.commit()
    return stats
