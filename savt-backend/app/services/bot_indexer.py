import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import delete, or_, select
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



# Отсекаем совсем мелкие картинки (иконки, логотипы, декоративные элементы) —
# не тратим на них запросы к платным моделям, там всё равно нет полезного
# для поиска содержимого
_MIN_DOC_IMAGE_DIMENSION = 150
# Защита от неограниченных расходов на документ с кучей картинок — считается
# на весь документ, не на страницу
_MAX_DOC_IMAGES_ANALYZED = 15

_DOC_IMAGE_PROMPT = (
    "Это изображение со страницы технической документации на шкаф управления "
    "(схема, чертёж, фото компонента и т.п.). Опиши его для инженера: что "
    "изображено, какие обозначения/подписи видны, какая связь между "
    "элементами показана. Кратко и по делу, на русском языке."
)


def _doc_image_mime_type(img) -> str:
    fmt = (getattr(img.image, "format", None) or "PNG").lower() if img.image else "png"
    return f"image/{fmt}" if fmt in ("png", "jpeg", "webp") else "image/png"


def _doc_image_too_small(img) -> bool:
    if img.image is None:
        return False
    width, height = img.image.width, img.image.height
    return width < _MIN_DOC_IMAGE_DIMENSION and height < _MIN_DOC_IMAGE_DIMENSION


async def _parse_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
    except Exception:
        logger.exception("Не удалось открыть PDF: %s", path)
        return ""

    parts: list[str] = []
    images_analyzed = 0
    for page_num, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(text)

        try:
            images = list(page.images)
        except Exception:
            images = []

        for img in images:
            if images_analyzed >= _MAX_DOC_IMAGES_ANALYZED:
                break
            if _doc_image_too_small(img):
                continue

            if not text:
                # Страница без текстового слоя — похоже на скан печатного
                # текста, OCR тут полезнее описания
                try:
                    ocr_text = await yandex_service.ocr_image(img.data)
                    if ocr_text.strip():
                        parts.append(ocr_text)
                except Exception:
                    logger.exception(
                        "OCR не удался для страницы %d файла %s", page_num + 1, path
                    )
            else:
                # Страница с текстом плюс картинка — обычно схема/фото рядом с
                # описанием, тут полезнее не OCR, а содержательное описание
                try:
                    description = await yandex_service.analyze_image(
                        img.data, _DOC_IMAGE_PROMPT, mime_type=_doc_image_mime_type(img),
                    )
                    if description.strip():
                        parts.append(f"[Изображение на стр. {page_num + 1}]: {description}")
                except Exception:
                    logger.exception(
                        "Анализ изображения не удался для страницы %d файла %s", page_num + 1, path
                    )

            images_analyzed += 1
            await asyncio.sleep(0.2)
    return "\n\n".join(parts)


def _docx_image_blobs(doc) -> list[tuple[bytes, str]]:
    """(байты, расширение) всех встроенных картинок документа — через
    relationships пакета .docx (doc.part.rels), не через обход XML вручную.
    Расширение берём из partname (реальное имя файла картинки внутри .docx,
    например "media/image5.emf") — по нему, а не по гаданию, понимаем, что
    Pillow не откроет и нужна конвертация через LibreOffice.

    В отличие от PDF, .docx не хранит разбивку по страницам на уровне файла
    (это только при печати/просмотре), поэтому "на какой странице" картинка —
    не определить."""
    blobs = []
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                part = rel.target_part
                ext = Path(str(part.partname)).suffix.lstrip(".").lower() or "png"
                blobs.append((part.blob, ext))
            except Exception:
                continue
    return blobs


def _probe_image_bytes(data: bytes) -> tuple[int, int, str]:
    """(ширина, высота, mime_type) картинки по байтам. (0, 0, ...) — если PIL
    не смог открыть (например, EMF/WMF — Pillow их не читает вообще)."""
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "PNG").lower()
            mime = f"image/{fmt}" if fmt in ("png", "jpeg", "webp") else "image/png"
            return img.width, img.height, mime
    except Exception:
        return 0, 0, "image/png"


async def _convert_image_via_libreoffice(data: bytes, src_ext: str) -> bytes | None:
    """PIL-нечитаемый формат (EMF/WMF и т.п.) → PNG через LibreOffice, чтобы
    такие картинки не выпадали из анализа молча. Переиспользует тот же
    _convert_legacy_office, что и .doc/.xls — там только subprocess.run с
    другим --convert-to, ему всё равно, документ это или картинка."""
    tmp_path = Path(tempfile.mktemp(suffix=f".{src_ext}"))
    try:
        await asyncio.to_thread(tmp_path.write_bytes, data)
        return await _convert_legacy_office(tmp_path, "png")
    finally:
        await asyncio.to_thread(tmp_path.unlink, True)


async def _parse_docx(path: Path) -> str:
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument(str(path))
        parts = [p.text for p in doc.paragraphs]
        # Технические характеристики в таких документах часто оформлены
        # таблицами — doc.paragraphs их не видит, обходим отдельно.
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
    except Exception:
        logger.exception("Не удалось разобрать Word-документ: %s", path)
        return ""

    try:
        images = _docx_image_blobs(doc)
    except Exception:
        images = []

    analyzed = 0
    for data, ext in images:
        if analyzed >= _MAX_DOC_IMAGES_ANALYZED:
            break
        width, height, mime_type = _probe_image_bytes(data)
        if width == 0 and height == 0:
            # Pillow не смог открыть — скорее всего EMF/WMF. Пробуем
            # сконвертировать в PNG через LibreOffice прежде, чем сдаться.
            converted = await _convert_image_via_libreoffice(data, ext)
            if converted is None:
                logger.warning(
                    "Картинка формата .%s в %s не читается ни Pillow, ни LibreOffice — пропущена", ext, path,
                )
                continue
            data = converted
            width, height, mime_type = _probe_image_bytes(data)
            if width == 0 and height == 0:
                logger.warning("Картинка формата .%s в %s не открылась даже после конвертации", ext, path)
                continue
        if width < _MIN_DOC_IMAGE_DIMENSION and height < _MIN_DOC_IMAGE_DIMENSION:
            continue
        try:
            description = await yandex_service.analyze_image(data, _DOC_IMAGE_PROMPT, mime_type=mime_type)
            if description.strip():
                parts.append(f"[Изображение в документе]: {description}")
        except Exception:
            logger.exception("Анализ изображения не удался для %s", path)
        analyzed += 1
        await asyncio.sleep(0.2)

    return "\n".join(parts)


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

# Старые бинарные форматы (Word/Excel 97-2003) python-docx/openpyxl не читают
# в принципе — это не тот же формат в другой обёртке, а совсем другой (OLE,
# не XML). Конвертируем через LibreOffice headless в современный .docx/.xlsx,
# а дальше уже переиспользуем обычный _parse_docx/_parse_excel как есть.
_LEGACY_OFFICE_TARGETS = {".doc": "docx", ".xls": "xlsx"}
_LIBREOFFICE_TIMEOUT_SECONDS = 90


def _run_libreoffice_convert(path: Path, target_ext: str, out_dir: str, profile_dir: str) -> bytes | None:
    """Синхронная часть (subprocess.run) — вызывается через asyncio.to_thread.
    -env:UserInstallation с отдельным профилем на каждый вызов — иначе
    параллельные конвертации (несколько документов проиндексированы почти
    одновременно) конфликтуют за один и тот же профиль LibreOffice и падают."""
    try:
        result = subprocess.run(
            [
                "soffice", "--headless", "--norestore",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to", target_ext, "--outdir", out_dir, str(path),
            ],
            capture_output=True, timeout=_LIBREOFFICE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning("LibreOffice: превышено время конвертации %s", path)
        return None
    if result.returncode != 0:
        logger.warning(
            "LibreOffice не смог сконвертировать %s: %s", path, result.stderr.decode(errors="replace")[:500],
        )
        return None
    converted = Path(out_dir) / f"{path.stem}.{target_ext}"
    if not converted.exists():
        logger.warning("LibreOffice не создал ожидаемый файл для %s", path)
        return None
    return converted.read_bytes()


async def _convert_legacy_office(path: Path, target_ext: str) -> bytes | None:
    with tempfile.TemporaryDirectory(prefix="office_convert_") as out_dir, \
         tempfile.TemporaryDirectory(prefix="lo_profile_") as profile_dir:
        try:
            return await asyncio.to_thread(_run_libreoffice_convert, path, target_ext, out_dir, profile_dir)
        except Exception:
            logger.exception("Не удалось сконвертировать %s через LibreOffice", path)
            return None


async def _extract_text(file_url: str) -> str:
    relative = file_url.removeprefix("/static/")
    path = UPLOAD_ROOT / relative
    if not path.exists():
        logger.warning("Файл для индексации не найден на диске: %s", path)
        return ""

    suffix = path.suffix.lower()

    if suffix in _LEGACY_OFFICE_TARGETS:
        target_ext = _LEGACY_OFFICE_TARGETS[suffix]
        converted = await _convert_legacy_office(path, target_ext)
        if converted is None:
            logger.warning("Не удалось прочитать старый формат (%s) через LibreOffice: %s", suffix, path)
            return ""
        tmp_path = Path(tempfile.mktemp(suffix=f".{target_ext}"))
        try:
            await asyncio.to_thread(tmp_path.write_bytes, converted)
            return await _parse_docx(tmp_path) if target_ext == "docx" else _parse_excel(tmp_path)
        finally:
            await asyncio.to_thread(tmp_path.unlink, True)

    if suffix == ".pdf":
        text = await _parse_pdf(path)
    elif suffix == ".docx":
        text = await _parse_docx(path)
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
    # Служебный документ (is_internal) не должен быть виден пользователю вообще —
    # значит, и в поиске бота его содержимое всплывать не должно. Чистим любые
    # уже существующие эмбеддинги (документ мог быть проиндексирован раньше, до
    # того как его закрыли) и не создаём новые — это же убирает из поиска файлы,
    # подхваченные с NAS напрямую (см. import_new_files_from_nas), у них
    # is_internal=True с самого создания.
    if doc.is_internal:
        await session.execute(
            delete(Embedding).where(
                Embedding.source_type == "document",
                Embedding.source_id == doc.id,
            )
        )
        return
    text = await _extract_text(doc.file_url)
    # Не удалось извлечь текст (нераспознанный формат, битый файл, документ без
    # текстового слоя и т.п.) — индексируем хотя бы заголовок, чтобы бот вообще
    # знал о существовании файла, но помечаем это в meta. Без этой пометки такой
    # документ навсегда застревал бы "уже проиндексированным" (у него ведь есть
    # embeddings) — reindex_all(force=False) больше никогда не пытался бы
    # переизвлечь текст повторно, даже после починки самого экстрактора.
    extraction_failed = not text.strip()
    if extraction_failed:
        text = doc.title or ""
    await _upsert_chunks(
        session, "document", doc.id, _chunks(text),
        {"title": doc.title, "cabinet_id": doc.cabinet_id, "extraction_failed": extraction_failed},
    )


def schedule_reindex_document(doc_id: int) -> None:
    """Фоновая (best-effort) переиндексация одного документа — после создания
    через форму загрузки или после смены is_internal через PATCH. Отдельная
    сессия — вызывается уже после commit основного запроса, не должна его
    блокировать/ронять."""
    async def _task():
        from app.database import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as s:
                doc = await s.get(Document, doc_id)
                if doc:
                    await index_document(s, doc)
                    await s.commit()
        except Exception:
            logger.exception("Фоновая переиндексация документа %s не удалась", doc_id)
    asyncio.create_task(_task())


async def _resolve_project_document_scope(session: AsyncSession, project_id: int) -> tuple[set[int], set[int]]:
    """project_id + все его дочерние проекты (рекурсивно) → их ID и ID всех их
    ШУ — та же область, что видит бот в чате этого проекта (см.
    bot_service._resolve_project_scope, логика продублирована здесь, а не
    импортирована, чтобы не тянуть зависимость между индексатором и ботом)."""
    from app.models.cabinets import Cabinet as CabinetModel
    from app.models.project import Project as ProjectModel

    project_ids = {project_id}
    frontier = {project_id}
    while frontier:
        rows = (await session.execute(
            select(ProjectModel.id).where(
                ProjectModel.parent_project_id.in_(frontier), ProjectModel.deleted_at.is_(None),
            )
        )).scalars().all()
        new_ids = set(rows) - project_ids
        if not new_ids:
            break
        project_ids |= new_ids
        frontier = new_ids

    cabinet_rows = (await session.execute(
        select(CabinetModel.id).where(
            CabinetModel.project_id.in_(project_ids), CabinetModel.deleted_at.is_(None),
        )
    )).scalars().all()
    return project_ids, set(cabinet_rows)


async def reindex_all(
    session: AsyncSession, force: bool = False,
    scope: str = "all", project_id: int | None = None,
) -> dict:
    """Индексирует только ещё не проиндексированные записи.
    force=True — переиндексирует всё (старое поведение).

    scope — что индексировать: "all" (по умолчанию), "faq", "kb_article" или
    "document". project_id имеет смысл только вместе с scope="document" (или
    "all") — ограничивает документы этим проектом и его дочерними/ШУ, как и
    видит их бот в чате проекта; для FAQ/статей КБ (они не привязаны к
    проекту) параметр просто не используется.

    Каждый элемент коммитится отдельно и сам ловит свою ошибку: раньше весь
    прогон коммитился одним разом в конце, и сбой на одном документе (например,
    сетевая ошибка Yandex API на одном из многих чанков большого файла) откатывал
    вообще всё, включая уже успешно проиндексированные до него FAQ/статьи/
    документы этого же прогона. Теперь одна неудача попадает в stats["failed"]
    и не мешает остальным."""
    stats = {"faq": 0, "kb_article": 0, "document": 0, "skipped": 0, "failed": 0}

    if not force:
        rows = (await session.execute(
            select(Embedding.source_type, Embedding.source_id, Embedding.meta)
        )).all()
        # Документы с extraction_failed=True в already НЕ попадают — иначе
        # обычный "переиндексировать новое" (force=False) навсегда пропускал бы
        # документ, у которого когда-то не извлёкся текст (см. index_document)
        already = {
            (source_type, source_id) for source_type, source_id, meta in rows
            if not (source_type == "document" and (meta or {}).get("extraction_failed"))
        }
    else:
        already = set()

    if scope in ("all", "faq"):
        entries = (await session.execute(select(FaqEntry))).scalars().all()
        for e in entries:
            if ("faq", e.id) in already:
                stats["skipped"] += 1
                continue
            try:
                await index_faq_entry(session, e)
                await session.commit()
                stats["faq"] += 1
            except Exception:
                await session.rollback()
                stats["failed"] += 1
                logger.exception("Индексация FAQ %s не удалась", e.id)

    if scope in ("all", "kb_article"):
        articles = (await session.execute(select(KbArticle))).scalars().all()
        for a in articles:
            if ("kb_article", a.id) in already:
                stats["skipped"] += 1
                continue
            try:
                await index_kb_article(session, a)
                await session.commit()
                stats["kb_article"] += 1
            except Exception:
                await session.rollback()
                stats["failed"] += 1
                logger.exception("Индексация статьи КБ %s не удалась", a.id)

    if scope in ("all", "document"):
        if project_id is not None:
            project_ids, cabinet_ids = await _resolve_project_document_scope(session, project_id)
            doc_stmt = select(Document).where(
                or_(Document.project_id.in_(project_ids), Document.cabinet_id.in_(cabinet_ids))
            )
        else:
            doc_stmt = select(Document)
        docs = (await session.execute(doc_stmt)).scalars().all()
        for d in docs:
            if ("document", d.id) in already:
                stats["skipped"] += 1
                continue
            try:
                await index_document(session, d)
                await session.commit()
                stats["document"] += 1
            except Exception:
                await session.rollback()
                stats["failed"] += 1
                logger.exception("Индексация документа %s не удалась", d.id)

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
