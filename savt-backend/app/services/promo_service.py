import logging
import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.database import AsyncSessionLocal
from app.models.promo_message import PromoMessage
from app.models.promo_schedule_settings import PromoScheduleSettings
from app.repositories.notification import NotificationRepository
from app.services.push_service import send_push

logger = logging.getLogger(__name__)


# --- заготовки (CRUD, раньше жили в файле PROMO_MESSAGES_FILE) ---

async def list_messages(session: AsyncSession) -> list[PromoMessage]:
    result = await session.execute(select(PromoMessage).order_by(PromoMessage.id))
    return list(result.scalars().all())


async def create_message(session: AsyncSession, data: dict) -> PromoMessage:
    msg = PromoMessage(**data)
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def update_message(session: AsyncSession, message_id: int, changed: dict) -> PromoMessage:
    msg = await session.get(PromoMessage, message_id)
    if msg is None:
        raise NotFoundError("Заготовка не найдена")
    for field, value in changed.items():
        setattr(msg, field, value)
    await session.commit()
    await session.refresh(msg)
    return msg


async def delete_message(session: AsyncSession, message_id: int) -> None:
    msg = await session.get(PromoMessage, message_id)
    if msg is None:
        raise NotFoundError("Заготовка не найдена")
    await session.delete(msg)
    await session.commit()


async def pick_random(
    session: AsyncSession, exclude_id: int | None = None, message_ids: list[int] | None = None,
) -> PromoMessage | None:
    stmt = select(PromoMessage)
    if message_ids:
        stmt = stmt.where(PromoMessage.id.in_(message_ids))
    messages = list((await session.execute(stmt)).scalars().all())
    if not messages:
        return None
    # Не повторяем подряд одну и ту же, если есть из чего выбрать
    candidates = [m for m in messages if m.id != exclude_id] or messages
    return random.choice(candidates)


async def send_random(
    session: AsyncSession, *,
    role: str | None = None,
    message: PromoMessage | None = None,
    message_ids: list[int] | None = None,
    exclude_id: int | None = None,
) -> tuple[PromoMessage | None, int, int]:
    """Рассылает случайную (или заданную) заготовку. Возвращает
    (что отправили, скольким, скольким не стали).

    message_ids — сузить случайный выбор до этого набора (см.
    PromoScheduleSettings.message_ids); пусто/None — среди всех заготовок.

    Уважает переключатель promotional — как и обычная рассылка администратора.
    Пауза уведомлений при этом глушит только пуш: запись в истории появится,
    и человек увидит её, когда вернётся."""
    chosen = message or await pick_random(session, exclude_id=exclude_id, message_ids=message_ids)
    if chosen is None:
        return None, 0, 0

    repo = NotificationRepository(session)
    all_ids = await repo.get_all_user_ids(role)
    user_ids = await repo.filter_by_setting(all_ids, "promotional")

    data = {**(chosen.data or {}), "promo_id": chosen.id}
    for user_id in user_ids:
        await repo.create(
            user_id=user_id, type_="promotional",
            title=chosen.title, body=chosen.body, data=data,
        )
    await session.commit()

    for user_id in user_ids:
        await send_push(
            session, user_id, chosen.title, chosen.body, data,
            notification_type="promotional",
        )
    return chosen, len(user_ids), len(all_ids) - len(user_ids)


# --- расписание автоматической рассылки ---

async def get_or_create_schedule(session: AsyncSession) -> PromoScheduleSettings:
    """Настройки расписания — singleton, всегда одна строка (id=1). Заводится
    лениво при первом обращении, а не миграцией, чтобы поведение по умолчанию
    (enabled=False) было явным на уровне колонки, а не забытой строкой данных."""
    row = await session.get(PromoScheduleSettings, 1)
    if row is None:
        row = PromoScheduleSettings(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def update_schedule(session: AsyncSession, changed: dict) -> PromoScheduleSettings:
    row = await get_or_create_schedule(session)
    for field, value in changed.items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return row


async def run_scheduled_check() -> None:
    """Прогон раз в час (см. main.py, cron minute=0) — сам решает по настройкам
    из БД (PromoScheduleSettings), пора ли слать, а не по фиксированному часу
    из .env: расписание меняется из админки без рестарта сервера.

    Условия отправки: enabled=True, текущий час (UTC) совпадает с send_hour,
    и с последней отправки прошло не меньше interval_days (либо отправки
    ещё не было)."""
    async with AsyncSessionLocal() as session:
        row = await get_or_create_schedule(session)
        if not row.enabled:
            return

        now = datetime.now(timezone.utc)
        if now.hour != row.send_hour:
            return

        if row.last_sent_at is not None:
            elapsed_days = (now - row.last_sent_at).total_seconds() / 86400
            if elapsed_days < row.interval_days:
                return

        try:
            chosen, sent, skipped = await send_random(
                session, role="user",
                message_ids=row.message_ids, exclude_id=row.last_sent_message_id,
            )
        except Exception:
            logger.exception("Реклама: плановая рассылка не удалась")
            return

        if chosen is None:
            logger.warning(
                "Реклама: нечего рассылать — заготовок нет или выбранные id не найдены",
            )
            return

        row.last_sent_at = now
        row.last_sent_message_id = chosen.id
        await session.commit()
        logger.info("Реклама «%s»: отправлено %d, пропущено отписавшихся %d",
                    chosen.id, sent, skipped)
