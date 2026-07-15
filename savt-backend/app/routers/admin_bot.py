from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.user import User
from app.services.bot_indexer import prune_orphaned, reindex_all

router = APIRouter(prefix="/admin/bot", tags=["admin: bot"])


@router.post("/reindex")
async def reindex(
    force: bool = Query(False, description="true — переиндексировать всё, false — только новое"),
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    stats = await reindex_all(session, force=force)
    return {"status": "ok", "indexed": stats}


# Удалить embeddings, чей источник (FAQ/статья КБ/документ) больше не существует
# — например, после удаления категории (каскадно сносит статьи в обход
# сервисного delete(), который обычно чистит embeddings сам)
@router.post("/prune")
async def prune(
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    stats = await prune_orphaned(session)
    return {"status": "ok", "removed": stats}
