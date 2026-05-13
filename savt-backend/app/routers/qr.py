from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.repositories.cabinet import CabinetRepository
from app.services.qr_service import generate_qr

router = APIRouter(tags=["qr"])


class QrGenerateIn(BaseModel):
    data: str


@router.post("/qr/generate")
async def generate_custom_qr(
    payload: QrGenerateIn,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
):
    image_bytes = generate_qr(payload.data)
    return Response(content=image_bytes, media_type="image/png")


@router.get("/admin/cabinets/{cabinet_id}/qr")
async def get_cabinet_qr(
    cabinet_id: int,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    repo = CabinetRepository(session)
    cabinet = await repo.get_by_id(cabinet_id)
    if cabinet is None:
        raise NotFoundError("ШУ не найден")
    image_bytes = generate_qr(f"savt://cabinet/{cabinet.unique_code}")
    return Response(content=image_bytes, media_type="image/png")
