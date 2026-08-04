import json
import logging
import random
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
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


def pick_random(exclude_id: str | None = None) -> PromoMessageOut | None:
    messages = load_messages()
    if not messages:
        return None
    # Не повторяем подряд одну и ту же, если есть из чего выбрать
    candidates = [m for m in messages if m.id != exclude_id] or messages
    return random.choice(candidates)


async def send_random(
    session: AsyncSession, *, role: str | None = None, message: PromoMessageOut | None = None,
) -> tuple[PromoMessageOut | None, int, int]:
    """Рассылает случайную (или заданную) заготовку. Возвращает
    (что отправили, скольким, скольким не стали).

    Уважает переключатель promotional — как и обычная рассылка администратора.
    Пауза уведомлений при этом глушит только пуш: запись в истории появится,
    и человек увидит её, когда вернётся."""
    chosen = message or pick_random()
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


async def send_random_scheduled() -> None:
    """Ежедневный прогон. Включается PROMO_AUTO_SEND_HOUR; при пустой настройке
    задача вообще не регистрируется (см. main.py)."""
    async with AsyncSessionLocal() as session:
        try:
            chosen, sent, skipped = await send_random(session, role="user")
        except Exception:
            logger.exception("Реклама: плановая рассылка не удалась")
            return
    if chosen is None:
        logger.warning("Реклама: нечего рассылать — подборка пуста")
    else:
        logger.info("Реклама «%s»: отправлено %d, пропущено отписавшихся %d",
                    chosen.id, sent, skipped)


def auto_send_hour() -> int | None:
    """Час автоматической рассылки или None, если она выключена."""
    raw = (settings.promo_auto_send_hour or "").strip()
    if not raw:
        return None
    try:
        hour = int(raw)
    except ValueError:
        logger.warning("PROMO_AUTO_SEND_HOUR=%r — не число, автоматическая рассылка выключена", raw)
        return None
    if not 0 <= hour <= 23:
        logger.warning("PROMO_AUTO_SEND_HOUR=%d вне 0..23 — автоматическая рассылка выключена", hour)
        return None
    return hour
