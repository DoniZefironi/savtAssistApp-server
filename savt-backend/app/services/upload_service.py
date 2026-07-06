import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

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

MAX_ATTACHMENT_SIZE = 500 * 1024 * 1024  # 500 МБ
MAX_VOICE_SIZE = 25 * 1024 * 1024        # 25 МБ


@dataclass
class FileInfo:
    url: str
    file_size_bytes: int
    mime_type: str
    doc_type: str


async def save_attachment(file: UploadFile) -> str:
    return (await save_attachment_with_meta(file)).url


async def save_attachment_with_meta(file: UploadFile) -> FileInfo:
    mime = file.content_type or "application/octet-stream"
    if mime in _ATTACHMENT_TYPES:
        folder, ext = _ATTACHMENT_TYPES[mime]
    elif mime.startswith("image/"):
        sub = mime.split("/", 1)[1].split(";")[0].strip() or "bin"
        folder, ext = "photos", sub
    elif mime.startswith("video/"):
        sub = mime.split("/", 1)[1].split(";")[0].strip() or "bin"
        folder, ext = "videos", sub
    elif mime.startswith("audio/"):
        sub = mime.split("/", 1)[1].split(";")[0].strip() or "bin"
        folder, ext = "voices", sub
    else:
        folder = "files"
        ext = Path(file.filename).suffix.lstrip(".") if file.filename else ""
        if not ext:
            ext = "bin"
    url, size = await _save(file, folder, ext, MAX_ATTACHMENT_SIZE)
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
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return f"/static/{folder}/{filename}", size
