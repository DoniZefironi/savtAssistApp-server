"""Суточная квота на загрузки обычного пользователя (USER_UPLOAD_QUOTA_MB_PER_DAY).

Лимит запросов считается на IP, а размер файла ограничен только одним файлом, поэтому без квоты
один пользователь за минуту мог залить на диск гигабайты, а на том же диске лежит база.

Квота считается по скользящим суткам из журнала upload_log. Место резервируется до сохранения
файла (по заявленному размеру запроса) и под блокировкой пользователя в БД: иначе десяток
одновременных загрузок все вместе проходили бы проверку, пока ни одна ещё не записана. После
сохранения размер уточняется (finalize), при неудаче резерв снимается (release)."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.upload_log import UploadLog

_MB = 1024 * 1024
_WINDOW = timedelta(hours=24)
# Журнал нужен только за сутки; запас, чтобы по нему можно было разобрать недавние загрузки
_KEEP_DAYS = 30
# Ключ блокировки в БД — пара (пространство, id пользователя), чтобы не пересечься с другими
_LOCK_NAMESPACE = 7301


async def reserve(session: AsyncSession, user_id: int, declared_bytes: int) -> int | None:
    """Резервирует место под загрузку и возвращает номер записи журнала. None — квота выключена.
    Если за последние сутки пользователь уже загрузил (с этим файлом) больше положенного —
    429, ничего не записывается. Завершает транзакцию сессии: блокировка пользователя
    держится только на время проверки и записи."""
    limit = settings.user_upload_quota_mb_per_day * _MB
    if limit <= 0:
        return None

    await session.execute(select(func.pg_advisory_xact_lock(_LOCK_NAMESPACE, user_id)))
    used = (await session.execute(
        select(func.coalesce(func.sum(UploadLog.size_bytes), 0)).where(
            UploadLog.user_id == user_id,
            UploadLog.created_at > datetime.now(timezone.utc) - _WINDOW,
        )
    )).scalar_one()
    if used >= limit or used + declared_bytes > limit:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Суточный лимит загрузок исчерпан ({settings.user_upload_quota_mb_per_day} МБ). "
                   "Попробуйте позже",
        )

    entry = UploadLog(user_id=user_id, size_bytes=declared_bytes)
    session.add(entry)
    await session.flush()
    entry_id = entry.id
    await session.commit()
    return entry_id


async def finalize(session: AsyncSession, entry_id: int | None, size_bytes: int) -> None:
    """Заменяет заявленный размер резерва на фактический."""
    if entry_id is None:
        return
    await session.execute(update(UploadLog).where(UploadLog.id == entry_id).values(size_bytes=size_bytes))
    await session.commit()


async def release(session: AsyncSession, entry_id: int | None) -> None:
    """Снимает резерв: файл не сохранился, квота не тратится."""
    if entry_id is None:
        return
    await session.execute(delete(UploadLog).where(UploadLog.id == entry_id))
    await session.commit()


async def prune_old(session: AsyncSession) -> int:
    """Удаляет записи старше _KEEP_DAYS суток. Возвращает число удалённых."""
    result = await session.execute(
        delete(UploadLog).where(UploadLog.created_at < datetime.now(timezone.utc) - timedelta(days=_KEEP_DAYS))
    )
    await session.commit()
    return result.rowcount or 0
