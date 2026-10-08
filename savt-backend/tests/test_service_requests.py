"""Сервисные заявки: создание (доступ, снимок гарантии, идемпотентность, чат,
задача в Bitrix), списки, смена статуса (уведомление, архивация чата, Bitrix),
синхронизация статусов и сообщений из/в Bitrix. Bitrix не вызывается: функции
записываются, фоновые задачи выполняются явно через env.run_background()."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.audit_log import AuditLog
from app.models.chat import Chat
from app.models.notification import Notification
from app.models.service_request import ServiceRequest
from app.schemas.service_requests import ServiceRequestCreateIn, ServiceRequestStatusIn
from app.services import bitrix_service, project_folder_service, realtime_events, service_request_service
from app.services.service_request_service import (
    ServiceRequestService, _build_task_title, sync_message_to_bitrix, sync_single_task_status,
    sync_statuses_from_bitrix,
)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def env(db_session, monkeypatch):
    e = SimpleNamespace(
        spawned=[], tasks=[], status_pushes=[], comments=[], exports=[], created_chats=[],
        task_id="T-100", create_error=None, statuses={}, statuses_error=None,
    )

    def spawn(coro, **kwargs):
        e.spawned.append(coro)

    async def run_background():
        pending, e.spawned = e.spawned, []
        for coro in pending:
            await coro

    async def create_task(title, description):
        if e.create_error:
            raise e.create_error
        e.tasks.append((title, description))
        return e.task_id

    async def update_task_status(task_id, status):
        e.status_pushes.append((task_id, status))

    async def add_comment(task_id, text):
        e.comments.append((task_id, text))

    async def get_task_statuses(task_ids):
        if e.statuses_error:
            raise e.statuses_error
        return {tid: e.statuses[tid] for tid in task_ids if tid in e.statuses}

    async def publish_chat_created(chat_id, summary):
        e.created_chats.append(chat_id)

    e.run_background = run_background
    monkeypatch.setattr(service_request_service, "spawn", spawn)
    monkeypatch.setattr(bitrix_service, "create_task", create_task)
    monkeypatch.setattr(bitrix_service, "update_task_status", update_task_status)
    monkeypatch.setattr(bitrix_service, "add_comment", add_comment)
    monkeypatch.setattr(bitrix_service, "get_task_statuses", get_task_statuses)
    monkeypatch.setattr(realtime_events, "publish_chat_created", publish_chat_created)
    monkeypatch.setattr(project_folder_service, "schedule_request_chat_export", e.exports.append)
    monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: _SessionContext(db_session))
    yield e
    for coro in e.spawned:
        coro.close()


@pytest.fixture
def svc(db_session):
    return ServiceRequestService(db_session)


def cabinet_request(cabinet_id, **kw):
    return ServiceRequestCreateIn(
        cabinet_id=cabinet_id, request_type=kw.pop("request_type", "repair"),
        description=kw.pop("description", "Течёт насос"), **kw,
    )


def project_request(project_id, **kw):
    return ServiceRequestCreateIn(
        project_id=project_id, request_type=kw.pop("request_type", "diagnostics"),
        description=kw.pop("description", "Шумит вентилятор"), **kw,
    )


async def _chat_of(db_session, request_id):
    return (await db_session.execute(select(Chat).where(Chat.service_request_id == request_id))).scalar_one_or_none()


async def _notes(db_session, user_id):
    return list((await db_session.execute(
        select(Notification).where(Notification.user_id == user_id, Notification.type == "request_status")
        .order_by(Notification.id)
    )).scalars().all())


async def _member_with_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project, **cabinet_kw):
    user = await make_user(full_name="Иванов Иван", organization_name="ООО Ромашка")
    project = await make_project(name="Бизнес-центр", production_number="26_170")
    await link_user_project(user, project)
    cabinet = await make_cabinet(project_id=project.id, **cabinet_kw)
    return user, project, cabinet


# --- создание ---

async def test_cabinet_request_creates_chat_audit_and_bitrix_task(
    svc, db_session, make_user, make_project, make_cabinet, link_user_project, env,
):
    future = datetime.now(timezone.utc) + timedelta(days=200)
    user, project, cabinet = await _member_with_cabinet(
        db_session, make_user, make_project, make_cabinet, link_user_project,
        object_number="29_099", type="Вентиляционная установка", purpose="ПНС Вейно",
        admin_internal_name="П-228", warranty_ends_at=future,
    )

    out = await svc.create(user.id, cabinet_request(cabinet.id))

    assert out.cabinet_id == cabinet.id and out.status == "open" and out.is_under_warranty is True
    assert out.cabinet_object_number == "29_099" and out.bitrix_task_id is None
    chat = await _chat_of(db_session, out.id)
    assert chat is not None and out.chat_id == chat.id
    assert (chat.chat_type, chat.user_id, chat.cabinet_id, chat.bot_active) == ("service_request", user.id, cabinet.id, False)
    assert env.created_chats == [chat.id]
    [audit] = (await db_session.execute(select(AuditLog).where(AuditLog.action == "service_request.create"))).scalars().all()
    assert audit.entity_id == out.id and audit.actor_id == user.id and audit.payload["is_under_warranty"] is True
    assert env.tasks == []  # в Bitrix уйдёт фоном

    await env.run_background()

    [(title, body)] = env.tasks
    assert title == "[Гарантия] 29_099 ООО Ромашка ПНС Вейно (П-228) ремонт"
    assert "Заявка №" in body and "ШУ: 29_099 (Вентиляционная установка)" in body
    assert "Гарантия: да" in body and "От: Иванов Иван" in body and "Течёт насос" in body
    assert (await db_session.get(ServiceRequest, out.id)).bitrix_task_id == "T-100"


async def test_paid_request_is_marked_in_the_task(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    user, _, cabinet = await _member_with_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project)

    out = await svc.create(user.id, cabinet_request(cabinet.id))
    await env.run_background()

    assert out.is_under_warranty is False
    assert env.tasks[0][0].startswith("[ПЛАТНО] ")
    assert "нет (платное обслуживание)" in env.tasks[0][1]


async def test_project_request_uses_the_project_as_the_target(svc, db_session, make_user, make_project, link_user_project, env):
    user = await make_user(full_name="Петров Пётр")
    project = await make_project(name="Бизнес-центр Космос", production_number="26_170")
    await link_user_project(user, project)

    out = await svc.create(user.id, project_request(project.id))
    await env.run_background()

    assert out.project_id == project.id and out.cabinet_id is None and out.project_name == "Бизнес-центр Космос"
    title, body = env.tasks[0]
    assert title == "[ПЛАТНО] 26_170 Петров Пётр диагностика"
    assert "Проект: 26_170 Бизнес-центр Космос" in body
    chat = await _chat_of(db_session, out.id)
    assert chat.project_id == project.id and chat.cabinet_id is None


async def test_directly_added_cabinet_gives_the_right_to_create_a_request(svc, db_session, make_user, make_cabinet, link_user_cabinet, env):
    user = await make_user()
    cabinet = await make_cabinet()
    await link_user_cabinet(user, cabinet)

    out = await svc.create(user.id, cabinet_request(cabinet.id))

    assert out.cabinet_id == cabinet.id


async def test_no_access_means_no_request(svc, db_session, make_user, make_project, make_cabinet, env):
    stranger = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)

    with pytest.raises(PermissionDeniedError):
        await svc.create(stranger.id, cabinet_request(cabinet.id))
    with pytest.raises(PermissionDeniedError):
        await svc.create(stranger.id, project_request(project.id))

    assert (await db_session.execute(select(ServiceRequest))).scalars().all() == []
    assert env.spawned == []


async def test_repeated_client_token_returns_the_same_request(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    user, _, cabinet = await _member_with_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project)

    first = await svc.create(user.id, cabinet_request(cabinet.id, client_token="tmp-1"))
    second = await svc.create(user.id, cabinet_request(cabinet.id, description="Другой текст", client_token="tmp-1"))

    assert second.id == first.id and second.chat_id == first.chat_id
    assert len((await db_session.execute(select(ServiceRequest))).scalars().all()) == 1
    assert len(env.spawned) == 1  # задача в Bitrix заводится один раз


# --- списки ---

async def test_user_sees_only_their_own_requests_with_chat_ids(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    me, project, cabinet = await _member_with_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project)
    other = await make_user()
    await link_user_project(other, project)
    mine = await svc.create(me.id, cabinet_request(cabinet.id))
    await svc.create(other.id, cabinet_request(cabinet.id))

    page = await svc.list_for_user(me.id, None, 1, 20)

    assert [i.id for i in page.items] == [mine.id] and page.total == 1
    assert page.items[0].chat_id == mine.chat_id


async def test_user_list_filters_by_status_and_paginates(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    me, _, cabinet = await _member_with_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project)
    created = [await svc.create(me.id, cabinet_request(cabinet.id)) for _ in range(3)]
    await svc.update_status(created[0].id, ServiceRequestStatusIn(status="in_progress"))

    assert (await svc.list_for_user(me.id, "in_progress", 1, 20)).total == 1
    page = await svc.list_for_user(me.id, None, 1, 2)
    assert len(page.items) == 2 and page.total == 3 and page.pages == 2


async def test_admin_list_filters_and_shows_the_applicant(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    user, project, cabinet = await _member_with_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project)
    repair = await svc.create(user.id, cabinet_request(cabinet.id, request_type="repair"))
    await svc.create(user.id, project_request(project.id, request_type="diagnostics"))

    by_cabinet = await svc.list_admin(None, cabinet.id, 1, 20)
    by_type = await svc.list_admin(None, None, 1, 20, request_type="diagnostics")
    by_project = await svc.list_admin(None, None, 1, 20, project_id=project.id)

    assert [i.id for i in by_cabinet.items] == [repair.id]
    assert [i.request_type for i in by_type.items] == ["diagnostics"]
    assert by_project.total == 1
    applicant = by_cabinet.items[0]
    assert applicant.user_full_name == "Иванов Иван" and applicant.user_phone == user.phone
    assert applicant.chat_id == repair.chat_id


# --- смена статуса ---

async def _open_request(svc, db_session, make_user, make_project, make_cabinet, link_user_project):
    user, _, cabinet = await _member_with_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project)
    out = await svc.create(user.id, cabinet_request(cabinet.id))
    return user, out


async def test_status_change_is_recorded_and_the_applicant_is_told(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    user, out = await _open_request(svc, db_session, make_user, make_project, make_cabinet, link_user_project)
    admin = await make_user("admin")

    detail = await svc.update_status(out.id, ServiceRequestStatusIn(status="in_progress"), admin.id, "admin")

    assert detail.status == "in_progress" and detail.closed_at is None
    [audit] = (await db_session.execute(select(AuditLog).where(AuditLog.action == "service_request.status_change"))).scalars().all()
    assert (audit.actor_id, audit.actor_role) == (admin.id, "admin")
    assert audit.payload == {"old_status": "open", "new_status": "in_progress"}
    [note] = await _notes(db_session, user.id)
    assert note.title == "Заявка на обслуживание" and note.body == "Статус изменён: в работе"
    assert note.data["request_id"] == str(out.id) and note.data["chat_id"] == str(out.chat_id)


async def test_closing_archives_the_chat_and_reopening_restores_it(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    user, out = await _open_request(svc, db_session, make_user, make_project, make_cabinet, link_user_project)

    closed = await svc.update_status(out.id, ServiceRequestStatusIn(status="closed"))

    assert closed.closed_at is not None
    chat = await _chat_of(db_session, out.id)
    assert chat.archived_at is not None
    assert env.exports == [chat.id]  # стенограмма выгружается сразу

    reopened = await svc.update_status(out.id, ServiceRequestStatusIn(status="open"))

    assert reopened.closed_at is None and chat.archived_at is None
    assert env.exports == [chat.id]  # при открытии заново не выгружается


async def test_postponed_does_not_archive_the_chat(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    user, out = await _open_request(svc, db_session, make_user, make_project, make_cabinet, link_user_project)

    await svc.update_status(out.id, ServiceRequestStatusIn(status="postponed"))

    assert (await _chat_of(db_session, out.id)).archived_at is None


async def test_unknown_request_is_not_found(svc, env):
    with pytest.raises(NotFoundError):
        await svc.update_status(987654, ServiceRequestStatusIn(status="closed"))


async def test_closing_twice_keeps_the_first_closing_date_and_does_not_notify_again(
    svc, db_session, make_user, make_project, make_cabinet, link_user_project, env,
):
    user, out = await _open_request(svc, db_session, make_user, make_project, make_cabinet, link_user_project)
    first = await svc.update_status(out.id, ServiceRequestStatusIn(status="closed"))

    second = await svc.update_status(out.id, ServiceRequestStatusIn(status="closed"))

    assert second.closed_at == first.closed_at
    assert len(await _notes(db_session, user.id)) == 1
    logged = (await db_session.execute(select(AuditLog).where(AuditLog.action == "service_request.status_change"))).scalars().all()
    assert len(logged) == 1  # повтор без смены статуса в журнал не пишется


async def test_status_goes_to_bitrix_only_when_asked_and_linked(svc, db_session, make_user, make_project, make_cabinet, link_user_project, env):
    user, out = await _open_request(svc, db_session, make_user, make_project, make_cabinet, link_user_project)
    await svc.update_status(out.id, ServiceRequestStatusIn(status="in_progress"))
    await env.run_background()  # задача в Bitrix
    env.status_pushes.clear()
    (await db_session.get(ServiceRequest, out.id)).bitrix_task_id = "T-1"

    await svc.update_status(out.id, ServiceRequestStatusIn(status="postponed"), sync_to_bitrix=False)
    await env.run_background()
    assert env.status_pushes == []

    await svc.update_status(out.id, ServiceRequestStatusIn(status="closed"))
    await env.run_background()
    assert env.status_pushes == [("T-1", "closed")]


# --- опрос Bitrix ---

async def _linked(db_session, make_user, make_project, make_cabinet, link_user_project, task_id, status="open"):
    user, _, cabinet = await _member_with_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project)
    req = ServiceRequest(
        user_id=user.id, cabinet_id=cabinet.id, request_type="repair", is_under_warranty=False,
        description="Течёт", status=status, bitrix_task_id=task_id,
    )
    db_session.add(req)
    await db_session.flush()
    return user, req


@pytest.mark.parametrize("bitrix,expected", [
    ("3", "in_progress"), ("4", "in_progress"), ("6", "postponed"), ("5", "closed"), ("7", "closed"),
])
async def test_polling_applies_the_bitrix_status_without_pushing_it_back(
    db_session, make_user, make_project, make_cabinet, link_user_project, env, bitrix, expected,
):
    user, req = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-1")
    env.statuses = {"T-1": bitrix}

    await sync_statuses_from_bitrix()
    await env.run_background()

    assert req.status == expected
    assert env.status_pushes == []  # не отправляем обратно в Bitrix
    [audit] = (await db_session.execute(select(AuditLog).where(AuditLog.action == "service_request.status_change"))).scalars().all()
    assert (audit.actor_id, audit.actor_role) == (None, "bitrix")
    assert len(await _notes(db_session, user.id)) == 1


@pytest.mark.parametrize("bitrix", ["1", "2", "99", None])
async def test_polling_ignores_unchanged_or_unknown_statuses(db_session, make_user, make_project, make_cabinet, link_user_project, env, bitrix):
    user, req = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-1")
    if bitrix:
        env.statuses = {"T-1": bitrix}

    await sync_statuses_from_bitrix()

    assert req.status == "open" and await _notes(db_session, user.id) == []


async def test_polling_skips_closed_requests_and_requests_without_a_task(db_session, make_user, make_project, make_cabinet, link_user_project, env):
    _, closed = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-1", status="closed")
    _, plain = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, None)
    env.statuses = {"T-1": "3"}

    await sync_statuses_from_bitrix()

    assert closed.status == "closed" and plain.status == "open"


async def test_polling_survives_a_bitrix_outage(db_session, make_user, make_project, make_cabinet, link_user_project, env):
    _, req = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-1")
    env.statuses_error = RuntimeError("Bitrix недоступен")

    await sync_statuses_from_bitrix()  # не падает

    assert req.status == "open"


async def test_webhook_checks_one_task(db_session, make_user, make_project, make_cabinet, link_user_project, env):
    _, req = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-1")
    _, other = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-2")
    env.statuses = {"T-1": "3", "T-2": "3"}

    await sync_single_task_status("T-1")

    assert req.status == "in_progress" and other.status == "open"


async def test_webhook_ignores_unknown_and_closed_tasks_and_outages(db_session, make_user, make_project, make_cabinet, link_user_project, env):
    _, closed = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-1", status="closed")
    _, live = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-2")
    env.statuses = {"T-1": "3", "T-2": "3"}

    await sync_single_task_status("no-such")
    await sync_single_task_status("T-1")
    env.statuses_error = RuntimeError("сеть")
    await sync_single_task_status("T-2")

    assert closed.status == "closed" and live.status == "open"


# --- сообщения в Bitrix ---

async def test_message_is_mirrored_as_a_task_comment(db_session, make_user, make_project, make_cabinet, link_user_project, env):
    _, req = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, "T-5")

    sync_message_to_bitrix(req.id, "Иванов Иван", "Приезжайте завтра", ["http://x/a.jpg"])
    await env.run_background()

    assert env.comments == [("T-5", 'Иванов Иван написал: "Приезжайте завтра\nhttp://x/a.jpg"')]


async def test_message_without_a_task_is_not_sent(db_session, make_user, make_project, make_cabinet, link_user_project, env):
    _, req = await _linked(db_session, make_user, make_project, make_cabinet, link_user_project, None)

    sync_message_to_bitrix(req.id, "Иванов Иван", "Привет", [])
    sync_message_to_bitrix(987654, "Иванов Иван", "Привет", [])
    await env.run_background()

    assert env.comments == []


# --- заголовок задачи ---

@pytest.mark.parametrize("args,expected", [
    (("29_099", "ООО Ромашка", "ПНС Вейно", "П-228", "ремонт"), "29_099 ООО Ромашка ПНС Вейно (П-228) ремонт"),
    (("26_170", "Петров Пётр", None, None, "диагностика"), "26_170 Петров Пётр диагностика"),
    (("26_170", "", "Цех", "", "другое"), "26_170 Цех другое"),
])
def test_task_title_skips_missing_parts(args, expected):
    assert _build_task_title(*args) == expected
