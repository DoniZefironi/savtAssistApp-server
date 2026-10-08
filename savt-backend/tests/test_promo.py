"""Рекламные уведомления: заготовки (CRUD), рассылка, расписание автоматической
отправки. Firebase в тестах не настроен — пуш не уходит, проверяются записи в
истории уведомлений. Сам планировщик не запускается: run_scheduled_check
вызывается напрямую, время и сессия подменяются."""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.exceptions import NotFoundError
from app.models.notification import Notification
from app.models.notification_settings import NotificationSettings
from app.models.promo_message import PromoMessage
from app.services import promo_service

NOW = datetime(2026, 10, 8, 10, 15, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest_asyncio.fixture(autouse=True)
async def no_seeded_messages(db_session):
    """Миграции засевают таблицу заготовок — тесты считают выбор и рассылку
    только по своим заготовкам, поэтому начинаем с пустой таблицы (в рамках
    транзакции теста, после теста откатывается)."""
    await db_session.execute(delete(PromoMessage))
    await db_session.flush()


@pytest.fixture
def scheduler_env(db_session, monkeypatch):
    """run_scheduled_check открывает свою сессию и берёт текущее время — отдаём
    ему тестовую сессию и фиксированный момент (10:15 UTC)."""
    monkeypatch.setattr(promo_service, "AsyncSessionLocal", lambda: _SessionContext(db_session))
    monkeypatch.setattr(promo_service, "datetime", _FixedDatetime)


async def _promo(db_session, user):
    rows = (await db_session.execute(
        select(Notification).where(Notification.user_id == user.id, Notification.type == "promotional")
    )).scalars().all()
    return list(rows)


async def _message(db_session, title="Плановое ТО", **kw):
    return await promo_service.create_message(
        db_session, {"title": title, "body": f"Текст: {title}", "data": kw.get("data", {})},
    )


# --- заготовки ---

async def test_crud_of_messages(db_session):
    created = await promo_service.create_message(
        db_session, {"title": "Заголовок", "body": "Текст", "data": {"screen": "service_request"}},
    )
    assert created.id and created.data == {"screen": "service_request"}

    updated = await promo_service.update_message(db_session, created.id, {"title": "Новый"})
    assert updated.title == "Новый" and updated.body == "Текст"

    assert created.id in [m.id for m in await promo_service.list_messages(db_session)]

    await promo_service.delete_message(db_session, created.id)
    assert created.id not in [m.id for m in await promo_service.list_messages(db_session)]


async def test_update_and_delete_of_missing_message_are_not_found(db_session):
    with pytest.raises(NotFoundError):
        await promo_service.update_message(db_session, 987654, {"title": "x"})
    with pytest.raises(NotFoundError):
        await promo_service.delete_message(db_session, 987654)


# --- выбор заготовки ---

async def test_pick_random_without_messages_returns_none(db_session):
    assert await promo_service.pick_random(db_session) is None


async def test_pick_random_respects_message_ids(db_session):
    wanted = await _message(db_session, "Нужная")
    await _message(db_session, "Лишняя")

    for _ in range(10):
        picked = await promo_service.pick_random(db_session, message_ids=[wanted.id])
        assert picked.id == wanted.id


async def test_pick_random_does_not_repeat_previous_when_there_is_a_choice(db_session):
    first = await _message(db_session, "Первая")
    second = await _message(db_session, "Вторая")

    for _ in range(15):
        assert (await promo_service.pick_random(db_session, exclude_id=first.id)).id == second.id


async def test_pick_random_repeats_only_when_there_is_nothing_else(db_session):
    only = await _message(db_session, "Единственная")

    assert (await promo_service.pick_random(db_session, exclude_id=only.id)).id == only.id


async def test_message_ids_that_no_longer_exist_pick_nothing(db_session):
    await _message(db_session, "Есть")

    assert await promo_service.pick_random(db_session, message_ids=[987654]) is None


# --- рассылка ---

async def test_send_goes_to_active_users_who_did_not_opt_out(db_session, make_user):
    subscribed = await make_user()
    opted_out = await make_user()
    inactive = await make_user(is_active=False)
    db_session.add(NotificationSettings(user_id=opted_out.id, promotional=False))
    await db_session.flush()
    message = await _message(db_session, "Реклама", data={"screen": "kb"})

    chosen, sent, skipped = await promo_service.send_random(db_session, role="user", message=message)

    assert chosen.id == message.id
    assert sent >= 1 and skipped >= 1
    [received] = await _promo(db_session, subscribed)
    assert received.title == "Реклама"
    assert received.data == {"screen": "kb", "promo_id": message.id}
    assert await _promo(db_session, opted_out) == []
    assert await _promo(db_session, inactive) == []


async def test_send_limited_by_role(db_session, make_user):
    customer = await make_user()
    operator = await make_user("operator")
    message = await _message(db_session)

    await promo_service.send_random(db_session, role="operator", message=message)

    assert len(await _promo(db_session, operator)) == 1
    assert await _promo(db_session, customer) == []


async def test_send_without_messages_sends_nothing(db_session, make_user):
    user = await make_user()

    chosen, sent, skipped = await promo_service.send_random(db_session, role="user")

    assert (chosen, sent, skipped) == (None, 0, 0)
    assert await _promo(db_session, user) == []


# --- расписание ---

async def test_schedule_is_created_lazily_and_disabled_by_default(db_session):
    row = await promo_service.get_or_create_schedule(db_session)

    assert row.id == 1
    assert row.enabled is False
    assert row.interval_days == 1
    assert row.send_hour == 10
    assert row.message_ids is None and row.last_sent_at is None
    assert (await promo_service.get_or_create_schedule(db_session)).id == 1


async def test_update_schedule_changes_only_given_fields(db_session):
    await promo_service.update_schedule(db_session, {"enabled": True, "interval_days": 7})

    row = await promo_service.update_schedule(db_session, {"message_ids": [3, 4]})

    assert row.enabled is True and row.interval_days == 7 and row.message_ids == [3, 4]

    cleared = await promo_service.update_schedule(db_session, {"message_ids": None})
    assert cleared.message_ids is None


async def test_scheduled_check_does_nothing_when_disabled(db_session, make_user, scheduler_env):
    user = await make_user()
    await _message(db_session)
    await promo_service.update_schedule(db_session, {"enabled": False, "send_hour": NOW.hour})

    await promo_service.run_scheduled_check()

    assert await _promo(db_session, user) == []
    assert (await promo_service.get_or_create_schedule(db_session)).last_sent_at is None


async def test_scheduled_check_waits_for_the_configured_hour(db_session, make_user, scheduler_env):
    user = await make_user()
    await _message(db_session)
    await promo_service.update_schedule(db_session, {"enabled": True, "send_hour": (NOW.hour + 1) % 24})

    await promo_service.run_scheduled_check()

    assert await _promo(db_session, user) == []


async def test_scheduled_check_sends_and_remembers(db_session, make_user, scheduler_env):
    user = await make_user()
    operator = await make_user("operator")
    message = await _message(db_session)
    await promo_service.update_schedule(db_session, {"enabled": True, "send_hour": NOW.hour})

    await promo_service.run_scheduled_check()

    assert len(await _promo(db_session, user)) == 1
    assert await _promo(db_session, operator) == []  # автоматическая рассылка — только пользователям
    row = await promo_service.get_or_create_schedule(db_session)
    assert row.last_sent_at == NOW
    assert row.last_sent_message_id == message.id


async def test_scheduled_check_respects_interval(db_session, make_user, scheduler_env):
    user = await make_user()
    await _message(db_session)
    await promo_service.update_schedule(db_session, {
        "enabled": True, "send_hour": NOW.hour, "interval_days": 7,
        "last_sent_at": NOW - timedelta(days=3),
    })

    await promo_service.run_scheduled_check()
    assert await _promo(db_session, user) == []

    await promo_service.update_schedule(db_session, {"last_sent_at": NOW - timedelta(days=7, minutes=1)})
    await promo_service.run_scheduled_check()
    assert len(await _promo(db_session, user)) == 1


async def test_scheduled_check_does_not_resend_within_the_same_hour(db_session, make_user, scheduler_env):
    user = await make_user()
    await _message(db_session)
    await promo_service.update_schedule(db_session, {"enabled": True, "send_hour": NOW.hour, "interval_days": 1})

    await promo_service.run_scheduled_check()
    await promo_service.run_scheduled_check()

    assert len(await _promo(db_session, user)) == 1


async def test_scheduled_check_uses_only_selected_messages(db_session, make_user, scheduler_env):
    user = await make_user()
    await _message(db_session, "Не выбрана")
    chosen = await _message(db_session, "Выбрана")
    await promo_service.update_schedule(db_session, {
        "enabled": True, "send_hour": NOW.hour, "message_ids": [chosen.id],
    })

    await promo_service.run_scheduled_check()

    [received] = await _promo(db_session, user)
    assert received.data["promo_id"] == chosen.id


async def test_scheduled_check_does_not_repeat_previous_message(db_session, make_user, scheduler_env):
    user = await make_user()
    previous = await _message(db_session, "Прошлая")
    fresh = await _message(db_session, "Новая")
    await promo_service.update_schedule(db_session, {
        "enabled": True, "send_hour": NOW.hour, "last_sent_message_id": previous.id,
    })

    await promo_service.run_scheduled_check()

    [received] = await _promo(db_session, user)
    assert received.data["promo_id"] == fresh.id


async def test_scheduled_check_without_messages_leaves_schedule_untouched(db_session, make_user, scheduler_env, caplog):
    await make_user()
    await promo_service.update_schedule(db_session, {"enabled": True, "send_hour": NOW.hour})

    await promo_service.run_scheduled_check()

    assert (await promo_service.get_or_create_schedule(db_session)).last_sent_at is None
    assert "нечего рассылать" in caplog.text
