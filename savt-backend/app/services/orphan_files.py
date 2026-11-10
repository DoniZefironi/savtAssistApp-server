"""Ночная уборка файлов в /uploads, на которые больше не ссылается ни одна запись.

Такие файлы остаются от несостоявшихся сообщений (файл загрузили, сообщение не отправили), от
удалённых документов, фото и вложений базы знаний (запись стирается, файл нет), от повторных
загрузок. Сами по себе они не мешают, но диск растёт, а на нём же лежит база.

Файл удаляется только если выполнено всё сразу:
  - он лежит в одной из папок загрузок (photos, videos, voices, files, documents);
  - ему больше ORPHAN_FILES_MIN_AGE_DAYS суток по времени изменения: так не пропадёт файл,
    который загрузили, но ещё не прикрепили (например, сообщение из офлайн-очереди приложения);
  - на него не ссылается ни одна из колонок реестра (file_references.file_url_columns).
Записей в базе уборка не трогает. За один запуск удаляется не больше ORPHAN_FILES_MAX_PER_RUN
самых старых файлов. В режиме dry_run ничего не удаляется, а в лог пишется, что было бы удалено."""
import asyncio
import logging
import os
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import file_references, upload_service

logger = logging.getLogger(__name__)

# Папки, из которых UploadService раскладывает загрузки; всё остальное в /uploads уборка не трогает
FOLDERS = ("photos", "videos", "voices", "files", "documents")
_SECONDS_IN_DAY = 24 * 60 * 60
# Сколько имён файлов попадает в лог
_SAMPLE_SIZE = 20


def _list_old_files(root: Path, min_age_seconds: float) -> list[tuple[float, Path, int, str]]:
    """(время изменения, путь, размер, ссылка /static/...) всех достаточно старых файлов в папках
    загрузок, самые старые первыми. Синхронная: вызывается через asyncio.to_thread."""
    cutoff = time.time() - min_age_seconds
    found = []
    for folder in FOLDERS:
        directory = root / folder
        if not directory.is_dir():
            continue
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    continue
                info = entry.stat()
                if info.st_mtime <= cutoff:
                    found.append((info.st_mtime, Path(entry.path), info.st_size, f"/static/{folder}/{entry.name}"))
    found.sort(key=lambda item: item[0])
    return found


def _mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} МБ"


async def run_cleanup(session: AsyncSession) -> dict:
    """Один проход уборки. Возвращает статистику (она же пишется в лог)."""
    mode = settings.orphan_files_mode
    stats = {"mode": mode, "checked": 0, "orphans": 0, "orphan_bytes": 0,
             "deleted": 0, "deleted_bytes": 0, "limited": False}
    if mode == "off":
        return stats
    deleting = mode == "delete"      # любое другое значение работает как dry_run

    candidates = await asyncio.to_thread(
        _list_old_files, upload_service.UPLOAD_ROOT, settings.orphan_files_min_age_days * _SECONDS_IN_DAY,
    )
    stats["checked"] = len(candidates)
    if not candidates:
        logger.info("Уборка файлов (%s): старых файлов нет", mode)
        return stats

    # Ссылки читаются уже после списка файлов: свежая ссылка важнее устаревшего списка
    used = await file_references.referenced_urls(session)
    orphans = [item for item in candidates if item[3] not in used]
    stats["orphans"] = len(orphans)
    stats["orphan_bytes"] = sum(item[2] for item in orphans)

    batch = orphans[:settings.orphan_files_max_per_run]
    stats["limited"] = len(orphans) > len(batch)

    if deleting:
        for _mtime, _path, size, url in batch:
            try:
                await asyncio.to_thread(upload_service.delete_uploaded_file, url)
            except Exception:
                logger.exception("Уборка файлов: не удалось удалить %s", url)
                continue
            stats["deleted"] += 1
            stats["deleted_bytes"] += size

    sample = ", ".join(url for *_rest, url in batch[:_SAMPLE_SIZE])
    logger.info(
        "Уборка файлов (%s): проверено старых %d, без ссылок %d (%s)%s. %s%s",
        mode, stats["checked"], stats["orphans"], _mb(stats["orphan_bytes"]),
        f", за запуск обработано не больше {settings.orphan_files_max_per_run}" if stats["limited"] else "",
        f"Удалено {stats['deleted']} ({_mb(stats['deleted_bytes'])})." if deleting else "Ничего не удалено, режим dry_run.",
        f" Первые: {sample}" if sample else "",
    )
    return stats
