from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from app.core.dependencies import get_current_user
from app.models.user import User
from app.services.upload_service import save_attachment, save_voice

router = APIRouter(prefix="/upload", tags=["upload"])


class UploadOut(BaseModel):
    url: str

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
