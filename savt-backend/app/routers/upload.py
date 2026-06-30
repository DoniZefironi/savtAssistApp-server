from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user
from app.models.user import User
from app.services.upload_service import UPLOAD_ROOT, get_wav_sample_rate, save_attachment, save_voice

router = APIRouter(prefix="/upload", tags=["upload"])

_STATIC_PREFIX = "/static/"

# Yandex SpeechKit stt:recognize (синхронный) принимает максимум 1 МБ — берём с запасом
_MAX_STT_BYTES = 1_000_000


class UploadOut(BaseModel):
    url: str


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
    if not payload.file_url.startswith(_STATIC_PREFIX):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный URL")
    relative = payload.file_url[len(_STATIC_PREFIX):]
    file_path = Path(UPLOAD_ROOT / relative).resolve()
    if not str(file_path).startswith(str(UPLOAD_ROOT.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный URL")
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    ext = file_path.suffix.lower()
    audio_bytes = file_path.read_bytes()

    sample_rate_hertz: int | None = None
    if ext == ".wav":
        fmt = "lpcm"
        sample_rate_hertz = get_wav_sample_rate(audio_bytes)
        if sample_rate_hertz not in (8000, 16000, 48000):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неподдерживаемый WAV-файл: частота дискретизации должна быть 8000, 16000 или 48000 Гц",
            )
    else:
        _EXT_TO_STT_FMT = {
            ".mp3": "mp3", ".m4a": "mp3", ".aac": "mp3",
            ".ogg": "oggopus", ".oga": "oggopus", ".webm": "oggopus",
        }
        fmt = _EXT_TO_STT_FMT.get(ext, "oggopus")

    try:
        if len(audio_bytes) <= _MAX_STT_BYTES:
            text = await yandex_service.transcribe_voice(audio_bytes, format=fmt, sample_rate_hertz=sample_rate_hertz)
        else:
            text = await yandex_service.transcribe_voice_long(audio_bytes, format=fmt, sample_rate_hertz=sample_rate_hertz)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    return TranscribeOut(text=text)


# Скачать файл (фото, документ, голосовое) с заголовком attachment
@router.get("/download", tags=["upload"])
async def download_file(
    url: str = Query(..., description="URL вида /static/photos/uuid.jpg"),
    _: User = Depends(get_current_user),
):
    if not url.startswith(_STATIC_PREFIX):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный URL")
    relative = url[len(_STATIC_PREFIX):]
    file_path = Path(UPLOAD_ROOT / relative).resolve()
    if not str(file_path).startswith(str(UPLOAD_ROOT.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный URL")
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
    )
