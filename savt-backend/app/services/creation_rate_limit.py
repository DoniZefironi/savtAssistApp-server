"""Предел на число заявок от одного пользователя в минуту (USER_REQUESTS_PER_MINUTE).

Это защита от сбоя, а не ограничение для людей: быстрее десяти заявок в минуту человек не
подаёт, а зациклившееся приложение или скрипт успевают завести сотни. Каждая сервисная
заявка — задача в Bitrix, каждая рекламация — карточка в журнале, и вычищать их оттуда
пришлось бы руками. Общий лимит запросов считается на IP и на порядки выше.

Сервисные заявки и рекламации считаются отдельно, по своим таблицам.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import RoleName
from app.core.exceptions import RateLimitError

_WINDOW = timedelta(minutes=1)
# Свой номер блокировки, чтобы не пересекаться с квотой загрузок (upload_quota)
_LOCK_NAMESPACE = 7302


async def ensure_creation_allowed(session: AsyncSession, model, user_id: int, role_name: str) -> None:
    """model — ServiceRequest или Reclamation. Сотрудников предел не касается.

    Блокировка держится до конца транзакции, то есть до сохранения самой заявки: параллельные
    запросы одного пользователя идут по очереди и видят уже созданные заявки."""
    limit = settings.user_requests_per_minute
    if limit <= 0 or role_name != RoleName.USER.value:
        return

    await session.execute(select(func.pg_advisory_xact_lock(_LOCK_NAMESPACE, user_id)))
    recent = (await session.execute(
        select(func.count()).select_from(model).where(
            model.user_id == user_id,
            model.created_at > datetime.now(timezone.utc) - _WINDOW,
        )
    )).scalar_one()
    if recent >= limit:
        raise RateLimitError("Слишком много заявок подряд. Попробуйте через минуту")
