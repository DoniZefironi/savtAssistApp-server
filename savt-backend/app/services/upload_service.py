import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

UPLOAD_ROOT = Path("/code/uploads")

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
    if file.content_type not in _ATTACHMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Неподдерживаемый тип файла: {file.content_type}",
        )
    folder, ext = _ATTACHMENT_TYPES[file.content_type]
    url, size = await _save(file, folder, ext, MAX_ATTACHMENT_SIZE)
    doc_type = _MIME_TO_DOC_TYPE.get(file.content_type, "other")
    return FileInfo(url=url, file_size_bytes=size, mime_type=file.content_type, doc_type=doc_type)


async def save_voice(file: UploadFile) -> str:
    if file.content_type not in _VOICE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Неподдерживаемый формат голосового сообщения: {file.content_type}",
        )
    ext = _VOICE_TYPES[file.content_type]
    url, _ = await _save(file, "voices", ext, MAX_VOICE_SIZE)
    return url


async def _save(file: UploadFile, folder: str, ext: str, max_size: int) -> tuple[str, int]:
    content = await file.read()

    if len(content) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл слишком большой. Максимум: {max_size // (1024 * 1024)} МБ",
        )

    filename = f"{uuid.uuid4().hex}.{ext}"
    dest = UPLOAD_ROOT / folder
    dest.mkdir(parents=True, exist_ok=True)
    (dest / filename).write_bytes(content)

    return f"/static/{folder}/{filename}", len(content)
