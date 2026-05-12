import uuid
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

MAX_ATTACHMENT_SIZE = 500 * 1024 * 1024  # 500 МБ
MAX_VOICE_SIZE = 25 * 1024 * 1024        # 25 МБ

# Сохранение вложений
async def save_attachment(file: UploadFile) -> str:
    if file.content_type not in _ATTACHMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Неподдерживаемый тип файла: {file.content_type}",
        )

    folder, ext = _ATTACHMENT_TYPES[file.content_type]
    return await _save(file, folder, ext, MAX_ATTACHMENT_SIZE)

# Сохранение ГС
async def save_voice(file: UploadFile) -> str:
    if file.content_type not in _VOICE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Неподдерживаемый формат голосового сообщения: {file.content_type}",
        )

    ext = _VOICE_TYPES[file.content_type]
    return await _save(file, "voices", ext, MAX_VOICE_SIZE)

# Сохранение
async def _save(file: UploadFile, folder: str, ext: str, max_size: int) -> str:
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

    return f"/static/{folder}/{filename}"
