import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.config import settings
from app.core.constants import STAFF_ROLE_NAMES
from app.services import antivirus_service

UPLOAD_ROOT = Path("/code/uploads")

# Расширение попадает в имя файла на диске и в публичный URL —
# пропускаем только простые ("jpg", "pdf"), всё подозрительное станет "bin"
_SAFE_EXT_RE = re.compile(r"^[a-z0-9]{1,10}$")


def _sanitize_ext(ext: str) -> str:
    ext = ext.lower().strip()
    return ext if _SAFE_EXT_RE.match(ext) else "bin"


def transcode_to_ogg_opus(audio_bytes: bytes) -> bytes:
    """Перекодирует голосовое (любой контейнер/кодек — webm/m4a/aac/wav/ogg/mp3)
    в OGG/Opus через ffmpeg. Yandex SpeechKit принимает oggopus гарантированно,
    в отличие от попыток угадать формат по расширению присланного файла."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0",
                "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-f", "ogg", "pipe:1",
            ],
            input=audio_bytes,
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось обработать аудиофайл: превышено время обработки",
        )
    if result.returncode != 0 or not result.stdout:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось обработать аудиофайл: неподдерживаемый формат",
        )
    return result.stdout

_ATTACHMENT_TYPES: dict[str, tuple[str, str]] = {
    "image/jpeg":    ("photos",    "jpg"),
    "image/png":     ("photos",    "png"),
    "image/webp":    ("photos",    "webp"),
    "video/mp4":     ("videos",    "mp4"),
    "video/quicktime": ("videos",  "mov"),
    "application/pdf": ("documents", "pdf"),
    "application/msword": ("documents", "doc"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("documents", "docx"),
    "application/vnd.ms-excel": ("documents", "xls"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("documents", "xlsx"),
}

_VOICE_TYPES: dict[str, str] = {
    "audio/ogg":   "ogg",
    "audio/mpeg":  "mp3",
    "audio/mp4":   "m4a",
    "audio/wav":   "wav",
    "audio/webm":  "webm",
    "video/webm":  "webm",   # Chrome иногда отдаёт video/webm для записи голоса
    "audio/aac":   "aac",
    "audio/x-m4a": "m4a",
}

# doc_type определяется автоматически по mime-типу
_MIME_TO_DOC_TYPE: dict[str, str] = {
    "application/pdf": "pdf",
    "application/msword": "word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/vnd.ms-excel": "excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "image/jpeg": "photo",
    "image/png": "photo",
    "image/webp": "photo",
    "video/mp4": "video",
    "video/quicktime": "video",
}

# Расширение файла определяет, как его отдаст nginx: .html или .svg из папок photos/videos/voices
# открылись бы в браузере как страница со скриптами на нашем домене. Тип файла присылает клиент
# (image/html — тоже «image/…»), поэтому подтип из MIME принимается только из этих списков,
# всё остальное сохраняется как .bin и отдаётся браузером как скачиваемый файл.
_SAFE_MEDIA_EXTS: dict[str, frozenset[str]] = {
    "photos": frozenset({"jpg", "jpeg", "png", "webp", "gif", "bmp", "heic", "heif", "avif"}),
    "videos": frozenset({"mp4", "mov", "webm", "mkv", "avi", "3gp", "mpeg", "m4v", "wmv"}),
    "voices": frozenset({"ogg", "mp3", "m4a", "wav", "webm", "aac", "opus", "flac", "amr", "mp4", "mpeg", "3gp", "3gpp"}),
}

# Исполняемые файлы и сценарии: клиенту они в чате не нужны, а сотрудник может открыть такой
# файл у себя на компьютере
_BLOCKED_EXTS = frozenset({
    "exe", "msi", "bat", "cmd", "com", "scr", "pif", "cpl", "dll", "sys", "lnk",
    "js", "jse", "vbs", "vbe", "wsf", "wsh", "ps1", "psm1", "hta", "reg", "jar", "apk", "sh",
})


def _media_ext(folder: str, mime_subtype: str) -> str:
    subtype = mime_subtype.split(";", 1)[0].strip().lower()
    return subtype if subtype in _SAFE_MEDIA_EXTS[folder] else "bin"


MAX_ATTACHMENT_SIZE = 500 * 1024 * 1024  # 500 МБ
MAX_VOICE_SIZE = 25 * 1024 * 1024        # 25 МБ


def exceeds_recognition_limit(path: Path) -> bool:
    """Распознавание целиком читает файл в память и гонит его через ffmpeg, а
    ссылку можно дать на любой файл из /uploads (вложения до 500 МБ), поэтому
    длиннее голосового, которое вообще можно загрузить, не обрабатываем."""
    return path.stat().st_size > MAX_VOICE_SIZE


@dataclass
class FileInfo:
    url: str
    file_size_bytes: int
    mime_type: str
    doc_type: str


def attachment_limit_for(role_name: str | None, mime_type: str | None = None) -> int:
    """Сотрудникам (оператор, администратор, суперадмин) до 500 МБ: документация, схемы, видео
    объектов. Остальным пользователям USER_UPLOAD_MAX_MB (по умолчанию 30 МБ), а видео, судя по
    присланному типу, USER_VIDEO_UPLOAD_MAX_MB (по умолчанию 100 МБ); не больше общего потолка."""
    if role_name in STAFF_ROLE_NAMES:
        return MAX_ATTACHMENT_SIZE
    is_video = (mime_type or "").startswith("video/")
    limit_mb = settings.user_video_upload_max_mb if is_video else settings.user_upload_max_mb
    return min(limit_mb * 1024 * 1024, MAX_ATTACHMENT_SIZE)


async def save_attachment(file: UploadFile, max_size: int | None = None) -> str:
    return (await save_attachment_with_meta(file, max_size)).url


async def save_attachment_with_meta(file: UploadFile, max_size: int | None = None) -> FileInfo:
    """max_size — предел для этого файла (см. attachment_limit_for); без него действует общий
    потолок MAX_ATTACHMENT_SIZE, так загружают документы и вложения базы знаний сотрудники."""
    mime = file.content_type or "application/octet-stream"
    if mime in _ATTACHMENT_TYPES:
        folder, ext = _ATTACHMENT_TYPES[mime]
    elif mime.startswith("image/"):
        folder, ext = "photos", _media_ext("photos", mime.split("/", 1)[1])
    elif mime.startswith("video/"):
        folder, ext = "videos", _media_ext("videos", mime.split("/", 1)[1])
    elif mime.startswith("audio/"):
        folder, ext = "voices", _media_ext("voices", mime.split("/", 1)[1])
    else:
        folder = "files"
        ext = Path(file.filename).suffix.lstrip(".") if file.filename else ""
        if not ext:
            ext = "bin"
    # Имя файла клиент пишет сам и может не совпасть с типом (photo.exe с типом image/png),
    # поэтому смотрим и на него
    filename_ext = Path(file.filename).suffix.lstrip(".").lower() if file.filename else ""
    if ext.lower() in _BLOCKED_EXTS or filename_ext in _BLOCKED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Файлы этого типа загружать нельзя",
        )
    url, size = await _save(file, folder, ext, min(max_size or MAX_ATTACHMENT_SIZE, MAX_ATTACHMENT_SIZE))
    doc_type = _MIME_TO_DOC_TYPE.get(mime, "other")
    return FileInfo(url=url, file_size_bytes=size, mime_type=mime, doc_type=doc_type)


async def save_voice(file: UploadFile) -> str:
    mime = file.content_type or ""
    if mime not in _VOICE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Неподдерживаемый формат голосового сообщения: {mime}. "
                   f"Поддерживаются: {', '.join(_VOICE_TYPES.keys())}",
        )
    ext = _VOICE_TYPES[mime]
    url, _ = await _save(file, "voices", ext, MAX_VOICE_SIZE)
    return url


def save_local_file(source: Path) -> FileInfo:
    """Кладёт в /uploads файл, уже лежащий на диске, и отдаёт те же метаданные,
    что и загрузка через HTTP. Используется обратной синхронизацией папок проекта:
    файл положили прямо в папку на NAS, а приложению нужна своя копия — источник
    истины для скачивания через API остаётся /uploads.

    Синхронная (shutil.copyfile) — вызывается из asyncio.to_thread."""
    import mimetypes
    import shutil

    mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    if mime in _ATTACHMENT_TYPES:
        folder, ext = _ATTACHMENT_TYPES[mime]
    elif mime.startswith("image/"):
        folder, ext = "photos", _media_ext("photos", mime.split("/", 1)[1])
    elif mime.startswith("video/"):
        folder, ext = "videos", _media_ext("videos", mime.split("/", 1)[1])
    elif mime.startswith("audio/"):
        folder, ext = "voices", _media_ext("voices", mime.split("/", 1)[1])
    else:
        folder = "files"
        ext = source.suffix.lstrip(".") or "bin"

    filename = f"{uuid.uuid4().hex}.{_sanitize_ext(ext)}"
    dest_dir = UPLOAD_ROOT / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    shutil.copyfile(source, dest)

    return FileInfo(
        url=f"/static/{folder}/{filename}",
        file_size_bytes=dest.stat().st_size,
        mime_type=mime,
        doc_type=_MIME_TO_DOC_TYPE.get(mime, "other"),
    )


def delete_uploaded_file(file_url: str | None) -> None:
    """Удаляет файл из /uploads по его публичному URL (`/static/...`).
    Best-effort: отсутствие файла не считается ошибкой.

    Сейчас единственный вызывающий (chat_service._schedule_attachment_cleanup)
    передаёт только доверенные URL, прочитанные из БД, а не произвольный вход —
    но resolve()+is_relative_to на границе всё равно нужен: та же защита, что
    у _resolve_static_path в upload.py, чтобы будущий вызывающий с менее
    доверенным входом не смог удалить файл за пределами UPLOAD_ROOT."""
    if not file_url:
        return
    target = (UPLOAD_ROOT / file_url.removeprefix("/static/")).resolve()
    if not target.is_relative_to(UPLOAD_ROOT.resolve()):
        return
    target.unlink(missing_ok=True)


_CHUNK_SIZE = 1024 * 1024  # 1 МБ


async def _save(file: UploadFile, folder: str, ext: str, max_size: int) -> tuple[str, int]:
    # Пишем на диск чанками: файл до 500 МБ не должен целиком висеть в памяти
    filename = f"{uuid.uuid4().hex}.{_sanitize_ext(ext)}"
    dest = UPLOAD_ROOT / folder
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / filename

    size = 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(_CHUNK_SIZE):
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Файл слишком большой. Максимум: {max_size // (1024 * 1024)} МБ",
                    )
                out.write(chunk)
        # Заражённый файл (или отказ при недоступном антивирусе) стирается тем же except ниже
        await antivirus_service.ensure_clean(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return f"/static/{folder}/{filename}", size
