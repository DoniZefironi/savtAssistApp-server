import asyncio
import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.bot_indexer import prune_orphaned, reindex_all

router = APIRouter(prefix="/admin/bot", tags=["admin: bot"])

_log = logging.getLogger(__name__)


# force=true на заметном объёме данных — это десятки/сотни синхронных вызовов
# Yandex API (эмбеддинги), запросто дольше proxy_read_timeout в nginx (504).
# Поэтому считаем в фоне и сразу отвечаем, а результат смотреть в логах.
@router.post("/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex(
    force: bool = Query(False, description="true — переиндексировать всё, false — только новое"),
    _: User = Depends(require_role(RoleName.ADMIN)),
):
    async def _task():
        try:
            async with AsyncSessionLocal() as session:
                stats = await reindex_all(session, force=force)
                _log.info("Переиндексация завершена (force=%s): %s", force, stats)
        except Exception:
            _log.exception("Переиндексация (force=%s) не удалась", force)
    asyncio.create_task(_task())
    return {"status": "started", "message": "Индексация запущена в фоне, результат смотрите в логах"}


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
