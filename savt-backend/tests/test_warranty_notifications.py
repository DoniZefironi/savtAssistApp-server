"""Уведомления об истечении гарантии ШУ (пороги 30, 10 и 1 день). Планировщик не
запускается — check_warranty_expiry вызывается напрямую, «сегодня» (по Минску) и
сессия подменяются; push не уходит (Firebase в тестах не настроен), проверяются
записи в истории уведомлений."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.notification_settings import NotificationSettings
from app.models.warranty_notif_log import WarrantyNotifLog
from app.schemas.cabinet import CabinetUpdateIn
from app.services import warranty_scheduler
from app.services.cabinet_service import CabinetService

TODAY = date(2026, 10, 8)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def clock(db_session, monkeypatch):
    """check_warranty_expiry узнаёт «сегодня» (по Минску) через _today и открывает
    свою сессию. Отдаём ему тестовую сессию и дату, которую тест может сдвигать."""
    state = {"today": TODAY}

    async def fake_today(session):
        return state["today"]

    monkeypatch.setattr(warranty_scheduler, "_today", fake_today)
    monkeypatch.setattr(warranty_scheduler, "AsyncSessionLocal", lambda: _SessionContext(db_session))
    return state


def ends_in(days: int, today: date = TODAY) -> datetime:
    day = today + timedelta(days=days)
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc)


async def _received(db_session, user):
    rows = (await db_session.execute(
        select(Notification).where(Notification.user_id == user.id, Notification.type == "warranty_expiring")
    )).scalars().all()
    return list(rows)


# --- склонение ---

@pytest.mark.parametrize("days,word", [
    (1, "день"), (2, "дня"), (3, "дня"), (4, "дня"), (5, "дней"), (10, "дней"), (11, "дней"),
    (12, "дней"), (14, "дней"), (21, "день"), (22, "дня"), (25, "дней"), (30, "дней"), (101, "день"),
])
def test_days_label(days, word):
    assert warranty_scheduler._days_label(days) == word


# --- основной сценарий ---

@pytest.mark.parametrize("days,word", [(30, "дней"), (10, "дней"), (1, "день")])
async def test_project_members_are_notified_at_each_threshold(
    db_session, make_user, make_project, make_cabinet, link_user_project, clock, days, word,
):
    project = await make_project()
    user_a, user_b = await make_user(), await make_user()
    await link_user_project(user_a, project)
    await link_user_project(user_b, project)
    cabinet = await make_cabinet(project_id=project.id, admin_internal_name="Главная подстанция",
                                 warranty_ends_at=ends_in(days))

    await warranty_scheduler.check_warranty_expiry()

    for user in (user_a, user_b):
        [note] = await _received(db_session, user)
        assert note.title == "Гарантия истекает"
        assert note.body == f"Гарантия ШУ «Главная подстанция» истекает через {days} {word}"
        assert note.data == {"cabinet_id": cabinet.id, "days_left": days}


async def test_cabinet_without_a_name_is_called_by_its_number(db_session, make_user, make_project, make_cabinet, link_user_project, clock):
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    await make_cabinet(project_id=project.id, object_number="29_099", admin_internal_name=None,
                       warranty_ends_at=ends_in(10))

    await warranty_scheduler.check_warranty_expiry()

    assert "«29_099»" in (await _received(db_session, user))[0].body


async def test_each_notice_is_sent_once(db_session, make_user, make_project, make_cabinet, link_user_project, clock):
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    cabinet = await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(30))

    await warranty_scheduler.check_warranty_expiry()
    await warranty_scheduler.check_warranty_expiry()

    assert len(await _received(db_session, user)) == 1
    logs = (await db_session.execute(select(WarrantyNotifLog).where(WarrantyNotifLog.cabinet_id == cabinet.id))).scalars().all()
    assert [log.days_before for log in logs] == [30]


async def test_the_whole_countdown_gives_three_notices(db_session, make_user, make_project, make_cabinet, link_user_project, clock):
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(30))

    for offset in (0, 20, 29):  # 30, 10 и 1 день до конца
        clock["today"] = TODAY + timedelta(days=offset)
        await warranty_scheduler.check_warranty_expiry()

    assert [n.data["days_left"] for n in await _received(db_session, user)] == [30, 10, 1]


@pytest.mark.parametrize("days", [0, 31, 60, -1, -3])
async def test_nothing_is_sent_on_the_last_day_after_it_or_long_before(db_session, make_user, make_project, make_cabinet, link_user_project, clock, days):
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(days))

    await warranty_scheduler.check_warranty_expiry()

    assert await _received(db_session, user) == []


async def test_cabinets_without_warranty_or_deleted_are_skipped(db_session, make_user, make_project, make_cabinet, link_user_project, clock):
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    await make_cabinet(project_id=project.id, warranty_ends_at=None)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(30), deleted_at=datetime.now(timezone.utc))

    await warranty_scheduler.check_warranty_expiry()

    assert await _received(db_session, user) == []


async def test_cabinet_without_project_and_users_is_harmless(db_session, make_cabinet, clock):
    cabinet = await make_cabinet(warranty_ends_at=ends_in(30))

    await warranty_scheduler.check_warranty_expiry()  # некого уведомлять — и не падает

    assert (await db_session.execute(select(WarrantyNotifLog).where(WarrantyNotifLog.cabinet_id == cabinet.id))).scalars().first()


async def test_user_who_turned_the_notice_off_gets_nothing_and_is_not_asked_again(
    db_session, make_user, make_project, make_cabinet, link_user_project, clock,
):
    project = await make_project()
    quiet, loud = await make_user(), await make_user()
    await link_user_project(quiet, project)
    await link_user_project(loud, project)
    db_session.add(NotificationSettings(user_id=quiet.id, warranty_expiring=False))
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(10))
    await db_session.flush()

    await warranty_scheduler.check_warranty_expiry()
    await warranty_scheduler.check_warranty_expiry()

    assert await _received(db_session, quiet) == []
    assert len(await _received(db_session, loud)) == 1


# --- кто считается «получателем» ---

async def test_user_with_a_directly_added_cabinet_is_notified(db_session, make_user, make_cabinet, link_user_cabinet, clock):
    """ШУ, добавленный по собственному QR (без проекта), — такой же доступ, и о
    его гарантии человек должен узнать."""
    owner = await make_user()
    cabinet = await make_cabinet(warranty_ends_at=ends_in(10))
    await link_user_cabinet(owner, cabinet)

    await warranty_scheduler.check_warranty_expiry()

    assert len(await _received(db_session, owner)) == 1


async def test_user_with_both_accesses_is_notified_once(
    db_session, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet, clock,
):
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    cabinet = await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(10))
    await link_user_cabinet(user, cabinet)

    await warranty_scheduler.check_warranty_expiry()

    assert len(await _received(db_session, user)) == 1


async def test_blocked_user_is_not_notified(db_session, make_user, make_project, make_cabinet, link_user_project, clock):
    project = await make_project()
    blocked, active = await make_user(is_active=False), await make_user()
    await link_user_project(blocked, project)
    await link_user_project(active, project)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(10))

    await warranty_scheduler.check_warranty_expiry()

    assert await _received(db_session, blocked) == []
    assert len(await _received(db_session, active)) == 1


async def test_members_of_a_deleted_project_are_not_notified(db_session, make_user, make_project, make_cabinet, link_user_project, clock):
    project = await make_project(deleted_at=datetime.now(timezone.utc))
    user = await make_user()
    await link_user_project(user, project)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(10))

    await warranty_scheduler.check_warranty_expiry()

    assert await _received(db_session, user) == []


# --- продление гарантии ---

async def _admin_update(db_session, make_user, cabinet, **fields):
    admin = await make_user("admin")
    await CabinetService(db_session).update(cabinet.id, CabinetUpdateIn(**fields), admin.id, "admin")


async def test_extended_warranty_is_announced_again(db_session, make_user, make_project, make_cabinet, link_user_project, clock):
    """После продления гарантии про новый срок должны снова предупредить: журнал
    «уже отправлено» относится к прежней дате окончания."""
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    cabinet = await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(30))
    await warranty_scheduler.check_warranty_expiry()
    assert len(await _received(db_session, user)) == 1

    new_end = ends_in(365)
    await _admin_update(db_session, make_user, cabinet, warranty_ends_at=new_end)
    clock["today"] = TODAY + timedelta(days=335)  # до нового конца 30 дней
    await warranty_scheduler.check_warranty_expiry()

    assert len(await _received(db_session, user)) == 2


async def test_unrelated_edit_does_not_trigger_a_repeat(db_session, make_user, make_project, make_cabinet, link_user_project, clock):
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    cabinet = await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(30))
    await warranty_scheduler.check_warranty_expiry()

    await _admin_update(db_session, make_user, cabinet, description="Новое описание")
    await _admin_update(db_session, make_user, cabinet, warranty_ends_at=cabinet.warranty_ends_at)  # та же дата
    await warranty_scheduler.check_warranty_expiry()

    assert len(await _received(db_session, user)) == 1


# --- наверстывание пропущенных дней ---

async def _notices(db_session, user):
    return [(n.data["days_left"], n.body) for n in await _received(db_session, user)]


async def _member(db_session, make_user, make_project, link_user_project):
    project = await make_project()
    user = await make_user()
    await link_user_project(user, project)
    return project, user


async def test_missed_threshold_day_is_made_up_with_the_real_days_left(
    db_session, make_user, make_project, make_cabinet, link_user_project, clock,
):
    """В день порога сервер не работал — предупреждаем при следующем запуске, и в
    тексте не «10 дней», а сколько осталось на самом деле."""
    project, user = await _member(db_session, make_user, make_project, link_user_project)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(10))
    clock["today"] = TODAY + timedelta(days=1)  # день порога пропустили, сегодня до конца 9 дней

    await warranty_scheduler.check_warranty_expiry()

    [(days_left, body)] = await _notices(db_session, user)
    assert days_left == 9 and body.endswith("через 9 дней")


async def test_new_cabinet_close_to_the_end_gets_one_notice_not_three(
    db_session, make_user, make_project, make_cabinet, link_user_project, clock,
):
    project, user = await _member(db_session, make_user, make_project, link_user_project)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(8))

    await warranty_scheduler.check_warranty_expiry()
    await warranty_scheduler.check_warranty_expiry()

    assert [d for d, _ in await _notices(db_session, user)] == [8]


async def test_cabinet_with_25_days_left_is_noticed_once_then_waits_for_the_next_threshold(
    db_session, make_user, make_project, make_cabinet, link_user_project, clock,
):
    project, user = await _member(db_session, make_user, make_project, link_user_project)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(25))

    for offset in (0, 1, 5, 14, 15, 20, 24):  # осталось 25, 24, 20, 11, 10, 5, 1
        clock["today"] = TODAY + timedelta(days=offset)
        await warranty_scheduler.check_warranty_expiry()

    assert [d for d, _ in await _notices(db_session, user)] == [25, 10, 1]


async def test_day_of_the_end_and_later_send_nothing_more(
    db_session, make_user, make_project, make_cabinet, link_user_project, clock,
):
    project, user = await _member(db_session, make_user, make_project, link_user_project)
    await make_cabinet(project_id=project.id, warranty_ends_at=ends_in(1))
    await warranty_scheduler.check_warranty_expiry()

    for offset in (1, 2, 30):
        clock["today"] = TODAY + timedelta(days=offset)
        await warranty_scheduler.check_warranty_expiry()

    assert [d for d, _ in await _notices(db_session, user)] == [1]


async def test_days_are_counted_by_minsk_midnight_not_utc(
    db_session, make_user, make_project, make_cabinet, link_user_project, clock,
):
    """Гарантия до 00:30 по Минску 19 октября — это 21:30 UTC 18-го. По UTC до
    конца 9 дней, по Минску — 10, и предупредить надо как раз за 10."""
    project, user = await _member(db_session, make_user, make_project, link_user_project)
    await make_cabinet(project_id=project.id, warranty_ends_at=datetime(2026, 10, 18, 21, 30, tzinfo=timezone.utc))
    clock["today"] = date(2026, 10, 9)

    await warranty_scheduler.check_warranty_expiry()

    [(days_left, body)] = await _notices(db_session, user)
    assert days_left == 10 and body.endswith("через 10 дней")


async def test_today_is_a_date_close_to_the_utc_date(db_session):
    today = await warranty_scheduler._today(db_session)

    # Минск опережает UTC на 3 часа: дата та же или на день позже
    utc_today = datetime.now(timezone.utc).date()
    assert today in (utc_today, utc_today + timedelta(days=1))
