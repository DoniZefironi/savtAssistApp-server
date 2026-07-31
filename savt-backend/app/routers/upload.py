import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.core.signed_urls import SignedUrl, strip_signature
from app.models.user import User
from app.services.upload_service import UPLOAD_ROOT, save_attachment, save_voice, transcode_to_ogg_opus

router = APIRouter(prefix="/upload", tags=["upload"])

_STATIC_PREFIX = "/static/"


# Клиент передаёт URL, полученный от нас, то есть с подписью в query. Здесь URL
# превращается в путь на диске — параметры подписи надо снять, иначе они попадут
# в имя файла. Заодно проверка выхода за UPLOAD_ROOT: startswith по строке
# пропускал бы соседний каталог вида /code/uploads_backup, поэтому is_relative_to.
def _resolve_static_path(url: str) -> Path:
    bare = strip_signature(url) or ""
    if not bare.startswith(_STATIC_PREFIX):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный URL")
    file_path = Path(UPLOAD_ROOT / bare[len(_STATIC_PREFIX):]).resolve()
    if not file_path.is_relative_to(UPLOAD_ROOT.resolve()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный URL")
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    return file_path

# Yandex SpeechKit stt:recognize (синхронный) принимает максимум 1 МБ — берём с запасом
_MAX_STT_BYTES = 1_000_000


class UploadOut(BaseModel):
    # Подписанный: по этому URL клиент сразу показывает превью загруженного
    # файла. Обратно он присылает его же — подпись снимается на входе,
    # в БД попадает голый путь (см. AttachmentIn.strip_url_signature)
    url: SignedUrl


class TranscribeIn(BaseModel):
    file_url: str = Field(..., description="URL голосового файла /static/voice/...")


class TranscribeOut(BaseModel):
    text: str

# Загрузить вложения
@router.post("/attachment", response_model=UploadOut)
async def upload_attachment(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
):
    return UploadOut(url=await save_attachment(file))

# Загрузить ГС
@router.post("/voice", response_model=UploadOut)
async def upload_voice(
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

    raw_bytes = file_path.read_bytes()
    audio_bytes = await asyncio.to_thread(transcode_to_ogg_opus, raw_bytes)

    try:
        if len(audio_bytes) <= _MAX_STT_BYTES:
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
