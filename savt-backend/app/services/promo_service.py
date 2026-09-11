import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.promo_schedule_settings import PromoScheduleSettings
from app.repositories.notification import NotificationRepository
from app.schemas.notifications import PromoMessageOut
from app.services.push_service import send_push

logger = logging.getLogger(__name__)

_DEFAULT_FILE = Path(__file__).resolve().parent.parent / "data" / "promo_messages.json"


def _messages_path() -> Path:
    return Path(settings.promo_messages_file) if settings.promo_messages_file else _DEFAULT_FILE


def _parse(raw: dict) -> list[PromoMessageOut]:
    messages = []
    for index, item in enumerate(raw.get("messages") or []):
        if not isinstance(item, dict):
            continue
        title, body = item.get("title"), item.get("body")
        if not title or not body:
            logger.warning("Реклама: запись %s без title/body — пропускаю", index)
            continue
        messages.append(PromoMessageOut(
            id=str(item.get("id") or index),
            title=str(title)[:255],
            body=str(body)[:1000],
            data={k: str(v) for k, v in (item.get("data") or {}).items()},
        ))
    return messages


def load_messages() -> list[PromoMessageOut]:
    """Читает подборку с диска на каждый вызов — файл правят руками, и держать
    его в памяти значило бы требовать перезапуск после каждой правки.

    Битый или отсутствующий файл — не повод ронять рассылку: возвращаем пустой
    список, вызывающий скажет об этом внятно."""
    path = _messages_path()
    try:
        raw = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        logger.warning("Реклама: файл %s не найден", path)
        return []
    except (OSError, json.JSONDecodeError):
        logger.exception("Реклама: не удалось прочитать %s", path)
        return []
    if not isinstance(raw, dict):
        logger.warning("Реклама: ожидался объект с ключом messages в %s", path)
        return []
    return _parse(raw)


def pick_random(
    exclude_id: str | None = None, message_ids: list[str] | None = None,
) -> PromoMessageOut | None:
    messages = load_messages()
    if message_ids:
        messages = [m for m in messages if m.id in message_ids]
    if not messages:
        return None
    # Не повторяем подряд одну и ту же, если есть из чего выбрать
    candidates = [m for m in messages if m.id != exclude_id] or messages
    return random.choice(candidates)


async def send_random(
    session: AsyncSession, *,
    role: str | None = None,
    message: PromoMessageOut | None = None,
    message_ids: list[str] | None = None,
    exclude_id: str | None = None,
) -> tuple[PromoMessageOut | None, int, int]:
    """Рассылает случайную (или заданную) заготовку. Возвращает
    (что отправили, скольким, скольким не стали).

    message_ids — сузить случайный выбор до этого набора (см.
    PromoScheduleSettings.message_ids); пусто/None — среди всех заготовок файла.

    Уважает переключатель promotional — как и обычная рассылка администратора.
    Пауза уведомлений при этом глушит только пуш: запись в истории появится,
    и человек увидит её, когда вернётся."""
    chosen = message or pick_random(exclude_id=exclude_id, message_ids=message_ids)
    if chosen is None:
        return None, 0, 0

    repo = NotificationRepository(session)
    all_ids = await repo.get_all_user_ids(role)
    user_ids = await repo.filter_by_setting(all_ids, "promotional")

    data = {**chosen.data, "promo_id": chosen.id}
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
                "Реклама: нечего рассылать — подборка пуста или выбранные заготовки не найдены в файле",
            )
            return

        row.last_sent_at = now
        row.last_sent_message_id = chosen.id
        await session.commit()
        logger.info("Реклама «%s»: отправлено %d, пропущено отписавшихся %d",
                    chosen.id, sent, skipped)
