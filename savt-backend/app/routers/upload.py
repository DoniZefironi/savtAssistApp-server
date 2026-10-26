import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.core.limiter import limiter
from app.core.signed_urls import SignedUrl, strip_signature, verify_signature
from app.models.role import Role
from app.models.user import User
from app.services.upload_service import (
    MAX_VOICE_SIZE,
    UPLOAD_ROOT,
    attachment_limit_for,
    exceeds_recognition_limit,
    save_attachment,
    save_voice,
    transcode_to_ogg_opus,
)

router = APIRouter(prefix="/upload", tags=["upload"])

_STATIC_PREFIX = "/static/"


# Клиент передаёт URL, полученный от нас, то есть с подписью в query. Здесь URL
# превращается в путь на диске — параметры подписи надо снять, иначе они попадут
# в имя файла. Заодно проверка выхода за UPLOAD_ROOT: startswith по строке
# пропускал бы соседний каталог вида /code/uploads_backup, поэтому is_relative_to.
#
# verify_signature — обязательна: эти два роута читают файл в обход nginx,
# напрямую в приложении, поэтому подпись/срок действия здесь должны
# проверяться так же, как их проверяет nginx для прямой раздачи /static/ —
# иначе Depends(get_current_user) остаётся единственной защитой, и любой
# залогиненный пользователь может скачать чужой файл по угаданному/утёкшему пути.
def _resolve_static_path(url: str) -> Path:
    if not verify_signature(url):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ссылка недействительна или истекла")
    bare = strip_signature(url) or ""
    if not bare.startswith(_STATIC_PREFIX):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный URL")
    file_path = Path(UPLOAD_ROOT / bare[len(_STATIC_PREFIX):]).resolve()
    if not file_path.is_relative_to(UPLOAD_ROOT.resolve()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный URL")
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    return file_path


class UploadOut(BaseModel):
    # Подписанный: по этому URL клиент сразу показывает превью загруженного
    # файла. Обратно он присылает его же — подпись снимается на входе,
    # в БД попадает голый путь (см. AttachmentIn.strip_url_signature)
    url: SignedUrl


class TranscribeIn(BaseModel):
    file_url: str = Field(..., description="URL голосового файла /static/voice/...")


class TranscribeOut(BaseModel):
    text: str

# Размер одного файла ограничен (upload_service: пользователям USER_UPLOAD_MAX_MB, сотрудникам
# 500 МБ), число загрузок в минуту — этим: иначе любой вошедший пользователь за минуту заливает
# десятки файлов и забивает диск. Лимит на IP, как и остальные (в офисе за общим NAT — на всех).
_UPLOADS_PER_MINUTE = "60/minute"

# Загрузить вложения
@router.post("/attachment", response_model=UploadOut)
@limiter.limit(_UPLOADS_PER_MINUTE)
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # Размер зависит от роли и вида файла: сотрудникам до 500 МБ, пользователям
    # USER_UPLOAD_MAX_MB, а видео USER_VIDEO_UPLOAD_MAX_MB
    role = await session.get(Role, user.role_id)
    limit = attachment_limit_for(role.name if role else None, file.content_type)
    return UploadOut(url=await save_attachment(file, limit))

# Загрузить ГС
@router.post("/voice", response_model=UploadOut)
@limiter.limit(_UPLOADS_PER_MINUTE)
async def upload_voice(
    request: Request,
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
):
    return UploadOut(url=await save_voice(file))

# Распознать ГС → текст (Yandex SpeechKit)
@router.post("/transcribe", response_model=TranscribeOut)
async def transcribe_voice(
    payload: TranscribeIn,
    _: User = Depends(get_current_user),
):
    from app.services import yandex_service
    file_path = _resolve_static_path(payload.file_url)
    if exceeds_recognition_limit(file_path):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл слишком большой для распознавания. Максимум: {MAX_VOICE_SIZE // (1024 * 1024)} МБ",
        )

    raw_bytes = file_path.read_bytes()
    audio_bytes = await asyncio.to_thread(transcode_to_ogg_opus, raw_bytes)

    try:
        if len(audio_bytes) <= yandex_service.MAX_SYNC_STT_BYTES:
            text = await yandex_service.transcribe_voice(audio_bytes, format="oggopus")
        else:
            text = await yandex_service.transcribe_voice_long(audio_bytes, format="oggopus")
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    return TranscribeOut(text=text)


# Скачать файл (фото, документ, голосовое) с заголовком attachment
@router.get("/download", tags=["upload"])
async def download_file(
    url: str = Query(..., description="URL вида /static/photos/uuid.jpg"),
    _: User = Depends(get_current_user),
):
    file_path = _resolve_static_path(url)
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
    )
