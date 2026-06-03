from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.user import User
from app.services.bot_indexer import reindex_all

router = APIRouter(prefix="/admin/bot", tags=["admin: bot"])


@router.post("/reindex")
async def reindex(
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    stats = await reindex_all(session)
    return {"status": "ok", "indexed": stats}
