"""ChatService, кроме отправки сообщения (она в test_chat_send_message.py):
правка и удаление сообщений, реакции, закрепы, прочтение, доступ к чатам,
архивация, списки, чаты проекта и заявок, настройки, поиск, действия оператора.
Realtime-события подменяются и записываются."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.models.chat import Chat
from app.models.message import Message
from app.models.message_attchment import MessageAttachment
from app.schemas.chat import ChatSettingsIn
from app.services import chat_service
from app.services.chat_service import MAX_PINS_PER_CHAT, ChatService


@pytest.fixture
def events(monkeypatch):
    log = []

    def recorder(name):
        async def _publish(*args, **kwargs):
            log.append((name, *args))
        return _publish

    for name in (
        "publish_chat_updated", "publish_message_created", "publish_message_deleted",
        "publish_message_pinned", "publish_message_unpinned", "publish_message_updated",
        "publish_messages_read", "publish_reaction_changed",
    ):
        monkeypatch.setattr(chat_service, name, recorder(name))
    return log


@pytest.fixture
def svc(db_session, events):
    return ChatService(db_session)


@pytest.fixture
def add_message(db_session):
    async def _add(chat, sender, text="Привет", **kw):
        msg = Message(chat_id=chat.id, sender_id=sender.id, text=text, **kw)
        db_session.add(msg)
        await db_session.flush()
        return msg
    return _add


@pytest.fixture
def cleanups(monkeypatch):
    urls = []
    monkeypatch.setattr(chat_service, "spawn", lambda coro, **kw: (urls.append(coro), coro.close()))
    return urls


# --- доступ ---

async def test_access_rules(svc, make_user, make_chat):
    owner, stranger = await make_user(), await make_user()
    operator = await make_user("operator")
    chat = await make_chat(owner, "support")
    notes = await make_chat(owner, "notes")

    assert await svc.has_access(chat.id, owner.id)
    assert await svc.has_access(chat.id, operator.id)
    assert not await svc.has_access(chat.id, stranger.id)
    assert not await svc.has_access(999999, owner.id)
    # личные заметки не видит даже оператор
    assert await svc.has_access(notes.id, owner.id)
    assert not await svc.has_access(notes.id, operator.id)


async def test_check_chat_access_uses_its_own_session(db_session, make_user, make_chat, monkeypatch):
    class Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: Ctx())
    owner = await make_user()
    chat = await make_chat(owner, "support")

    assert await chat_service.check_chat_access(chat.id, owner.id)
    assert not await chat_service.check_chat_access(chat.id, 999999)


# --- сообщения: правка и удаление ---

async def test_edit_message(svc, events, make_user, make_chat, add_message):
    user = await make_user()
    chat = await make_chat(user, "support")
    msg = await add_message(chat, user, "Старый")

    out = await svc.edit_message(chat.id, msg.id, user.id, "Новый")

    assert out.text == "Новый" and out.edited_at is not None
    assert [e[0] for e in events] == ["publish_message_updated"]


async def test_edit_message_restrictions(svc, make_user, make_chat, add_message):
    user, other = await make_user(), await make_user()
    operator = await make_user("operator")
    chat = await make_chat(user, "support")
    foreign = await add_message(chat, operator, "От оператора")
    other_chat = await make_chat(user, "notes")
    elsewhere = await add_message(other_chat, user)
    deleted = await add_message(chat, user, None, deleted_at=foreign.created_at)

    with pytest.raises(PermissionDeniedError):
        await svc.edit_message(chat.id, foreign.id, user.id, "Взлом")
    with pytest.raises(PermissionDeniedError):
        await svc.edit_message(chat.id, deleted.id, user.id, "Воскрешение")
    with pytest.raises(NotFoundError):
        await svc.edit_message(chat.id, elsewhere.id, user.id, "Не тот чат")
    with pytest.raises(NotFoundError):
        await svc.edit_message(chat.id, 999999, user.id, "Нет такого")
    with pytest.raises(PermissionDeniedError):
        await svc.edit_message(chat.id, foreign.id, other.id, "Посторонний")


async def test_delete_message(svc, events, db_session, make_user, make_chat, add_message):
    user = await make_user()
    chat = await make_chat(user, "support")
    msg = await add_message(chat, user)

    await svc.delete_message(chat.id, msg.id, user.id)

    assert msg.deleted_at is not None and msg.text is None
    assert events == [("publish_message_deleted", chat.id, msg.id)]


async def test_delete_message_restrictions(svc, make_user, make_chat, add_message):
    from datetime import datetime, timezone
    user = await make_user()
    operator = await make_user("operator")
    chat = await make_chat(user, "support")
    foreign = await add_message(chat, operator)
    mine = await add_message(chat, user)

    with pytest.raises(PermissionDeniedError):
        await svc.delete_message(chat.id, foreign.id, user.id)
    with pytest.raises(NotFoundError):
        await svc.delete_message(chat.id, 999999, user.id)
    chat.archived_at = datetime.now(timezone.utc)
    with pytest.raises(PermissionDeniedError):
        await svc.delete_message(chat.id, mine.id, user.id)


async def test_bulk_delete_skips_foreign_missing_and_already_deleted(svc, events, make_user, make_chat, add_message):
    user = await make_user()
    operator = await make_user("operator")
    chat = await make_chat(user, "support")
    mine_1, mine_2 = await add_message(chat, user), await add_message(chat, user)
    foreign = await add_message(chat, operator)

    deleted = await svc.bulk_delete_messages(chat.id, user.id, [mine_1.id, mine_2.id, foreign.id, 999999])
    again = await svc.bulk_delete_messages(chat.id, user.id, [mine_1.id])

    assert sorted(deleted) == sorted([mine_1.id, mine_2.id]) and again == []
    assert foreign.deleted_at is None
    assert len([e for e in events if e[0] == "publish_message_deleted"]) == 2


async def test_bulk_delete_in_archived_chat_is_refused(svc, make_user, make_chat, add_message):
    from datetime import datetime, timezone
    user = await make_user()
    chat = await make_chat(user, "support", archived_at=datetime.now(timezone.utc))
    msg = await add_message(chat, user)

    with pytest.raises(PermissionDeniedError):
        await svc.bulk_delete_messages(chat.id, user.id, [msg.id])


# --- чтение ---

async def test_get_messages_modes(svc, make_user, make_chat, add_message):
    user = await make_user()
    chat = await make_chat(user, "support")
    ids = [(await add_message(chat, user, f"сообщение {n}")).id for n in range(6)]

    latest = await svc.get_messages(chat.id, user.id, None, 3)
    before = await svc.get_messages(chat.id, user.id, ids[3], 10)
    after = await svc.get_messages(chat.id, user.id, None, 10, after_id=ids[3])
    around = await svc.get_messages(chat.id, user.id, None, 4, around_id=ids[3])
    found = await svc.get_messages(chat.id, user.id, None, 10, search="сообщение 4")

    assert [m.id for m in latest] == ids[:2:-1]  # новые → старые
    assert [m.id for m in before] == ids[2::-1]
    assert [m.id for m in after] == [ids[5], ids[4]]
    assert [m.id for m in around] == ids[1:]  # по limit//2 до и limit//2+1 от якоря
    assert [m.id for m in found] == [ids[4]]


async def test_get_messages_of_empty_chat_and_foreign_chat(svc, make_user, make_chat):
    user, stranger = await make_user(), await make_user()
    chat = await make_chat(user, "support")

    assert await svc.get_messages(chat.id, user.id, None, 10) == []
    with pytest.raises(PermissionDeniedError):
        await svc.get_messages(chat.id, stranger.id, None, 10)


async def test_mark_read_marks_only_foreign_messages_and_notifies(svc, events, make_user, make_chat, add_message):
    user = await make_user()
    operator = await make_user("operator")
    chat = await make_chat(user, "support")
    own = await add_message(chat, user, "моё")
    theirs = await add_message(chat, operator, "ответ")

    await svc.mark_read(chat.id, user.id)

    assert theirs.is_read is True and own.is_read is False
    assert [e[0] for e in events] == ["publish_messages_read", "publish_chat_updated"]
    assert events[0][2] == [theirs.id]


async def test_mark_read_without_news_stays_silent(svc, events, make_user, make_chat, add_message):
    user = await make_user()
    chat = await make_chat(user, "support")
    await add_message(chat, user)

    await svc.mark_read(chat.id, user.id)

    assert events == []


async def test_mark_read_summary_shows_deleted_marker(svc, events, make_user, make_chat, add_message):
    from datetime import datetime, timezone
    user = await make_user()
    operator = await make_user("operator")
    chat = await make_chat(user, "support")
    await add_message(chat, operator, "было")
    last = await add_message(chat, operator, None, deleted_at=datetime.now(timezone.utc))

    await svc.mark_read(chat.id, user.id)

    # удалённое сообщение не помечается прочитанным, а в сводке чата вместо текста — маркер
    assert last.is_read is False
    assert events[-1][0] == "publish_chat_updated"
    assert events[-1][2]["last_message_text"] == "Сообщение удалено"


# --- реакции ---

async def test_reactions(svc, events, db_session, make_user, make_chat, add_message):
    user, operator = await make_user(), await make_user("operator")
    chat = await make_chat(user, "support")
    msg = await add_message(chat, operator)

    await svc.add_reaction(chat.id, msg.id, user.id, "👍")
    with pytest.raises(AlreadyExistsError):
        await svc.add_reaction(chat.id, msg.id, user.id, "👍")
    await svc.add_reaction(chat.id, msg.id, user.id, "❤️")  # другая реакция того же человека — можно
    page = await svc.get_messages(chat.id, user.id, None, 10)
    assert sorted(r.emoji for r in page[0].reactions) == ["❤️", "👍"]

    await svc.remove_reaction(chat.id, msg.id, user.id, "👍")
    with pytest.raises(NotFoundError):
        await svc.remove_reaction(chat.id, msg.id, user.id, "👍")
    assert [e[0] for e in events].count("publish_reaction_changed") == 3


async def test_reaction_to_a_message_from_another_chat(svc, make_user, make_chat, add_message):
    user = await make_user()
    chat, other = await make_chat(user, "support"), await make_chat(user, "notes")
    msg = await add_message(other, user)

    with pytest.raises(NotFoundError):
        await svc.add_reaction(chat.id, msg.id, user.id, "👍")
    with pytest.raises(NotFoundError):
        await svc.remove_reaction(chat.id, msg.id, user.id, "👍")


# --- закреплённые сообщения ---

async def test_pin_messages(svc, events, make_user, make_chat, add_message):
    user = await make_user()
    chat = await make_chat(user, "support")
    first, second = await add_message(chat, user, "раз"), await add_message(chat, user, "два")

    await svc.pin_message(chat.id, first.id, user.id)
    pinned = await svc.pin_message(chat.id, second.id, user.id)
    await svc.pin_message(chat.id, second.id, user.id)  # повторно — без дубля и без события

    assert {m.id for m in pinned} == {first.id, second.id}
    assert len(await svc.get_pinned_messages(chat.id, user.id)) == 2
    assert [e[0] for e in events].count("publish_message_pinned") == 2

    left = await svc.unpin_message(chat.id, first.id, user.id)
    assert [m.id for m in left] == [second.id]
    assert await svc.unpin_all(chat.id, user.id) == []
    assert await svc.get_pinned_messages(chat.id, user.id) == []


async def test_pin_limit(svc, make_user, make_chat, add_message):
    user = await make_user()
    chat = await make_chat(user, "support")
    for n in range(MAX_PINS_PER_CHAT):
        await svc.pin_message(chat.id, (await add_message(chat, user, f"м{n}")).id, user.id)

    with pytest.raises(HTTPException) as err:
        await svc.pin_message(chat.id, (await add_message(chat, user, "лишнее")).id, user.id)
    assert err.value.status_code == 400


async def test_pin_foreign_message_and_stranger(svc, make_user, make_chat, add_message):
    user, stranger = await make_user(), await make_user()
    chat, other = await make_chat(user, "support"), await make_chat(user, "notes")
    elsewhere = await add_message(other, user)

    with pytest.raises(NotFoundError):
        await svc.pin_message(chat.id, elsewhere.id, user.id)
    with pytest.raises(PermissionDeniedError):
        await svc.pin_message(chat.id, elsewhere.id, stranger.id)
    with pytest.raises(PermissionDeniedError):
        await svc.get_pinned_messages(chat.id, stranger.id)


# --- закрепление чата в списке ---

async def test_pin_chat_is_personal(svc, events, make_user, make_chat):
    owner, operator = await make_user(), await make_user("operator")
    chat = await make_chat(owner, "support")

    await svc.pin_chat(chat.id, owner.id)
    owner_view = await svc.list_chats(owner.id)
    operator_view = await svc.list_operator_chats(operator.id)

    assert [c.is_pinned for c in owner_view] == [True]
    assert [c.is_pinned for c in operator_view if c.id == chat.id] == [False]
    await svc.unpin_chat(chat.id, owner.id)
    assert [c.is_pinned for c in await svc.list_chats(owner.id)] == [False]
    assert [e[0] for e in events] == ["publish_chat_updated"] * 2


# --- чаты проекта, ШУ и заявок ---

async def test_support_and_notes_are_created_once(svc, make_user):
    user = await make_user()

    first = await svc.ensure_support_and_notes(user.id)
    second = await svc.ensure_support_and_notes(user.id)

    assert first is not None and second is None


async def test_project_chat_requires_membership(svc, make_user, make_project, link_user_project):
    member, stranger = await make_user(), await make_user()
    project = await make_project()
    await link_user_project(member, project)

    chat = await svc.get_project_chat(member.id, project.id)
    again = await svc.get_project_chat(member.id, project.id)

    assert chat.id == again.id and chat.chat_type == "project"
    with pytest.raises(PermissionDeniedError):
        await svc.get_project_chat(stranger.id, project.id)


async def test_cabinet_chat_requires_access(svc, make_user, make_cabinet, link_user_cabinet):
    member, stranger = await make_user(), await make_user()
    cabinet = await make_cabinet()
    await link_user_cabinet(member, cabinet)

    chat = await svc.get_cabinet_chat(member.id, cabinet.id)
    again = await svc.get_cabinet_chat(member.id, cabinet.id)

    assert chat.id == again.id and chat.chat_type == "cabinet"
    with pytest.raises(PermissionDeniedError):
        await svc.get_cabinet_chat(stranger.id, cabinet.id)


@pytest.fixture
def make_request(db_session):
    from app.models.service_request import ServiceRequest

    async def _make(user, cabinet):
        sr = ServiceRequest(
            user_id=user.id, cabinet_id=cabinet.id, request_type="repair",
            is_under_warranty=False, description="Не включается", status="new",
        )
        db_session.add(sr)
        await db_session.flush()
        return sr
    return _make


async def test_service_request_chat_is_unique_and_has_no_bot(svc, make_user, make_cabinet, make_request):
    user = await make_user()
    cabinet = await make_cabinet()
    sr = await make_request(user, cabinet)

    chat = await svc.ensure_service_request_chat(user.id, sr.id, cabinet_id=cabinet.id)
    again = await svc.ensure_service_request_chat(user.id, sr.id, cabinet_id=cabinet.id)

    assert chat.id == again.id and chat.bot_active is False and chat.cabinet_id == cabinet.id


async def test_set_archived(svc, events, make_user, make_chat):
    user = await make_user()
    chat = await make_chat(user, "support")

    await svc.set_archived(chat.id, True)
    assert chat.archived_at is not None
    await svc.set_archived(chat.id, False)
    assert chat.archived_at is None
    assert await svc.set_archived(999999, True) is None
    assert [e[0] for e in events] == ["publish_chat_updated"] * 2


async def test_archiving_helpers_touch_only_the_right_chats(
    svc, make_user, make_chat, make_project, make_cabinet,
):
    user, other = await make_user(), await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    cabinet_chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)
    other_cabinet_chat = await make_chat(other, "cabinet", cabinet_id=cabinet.id)
    project_chat = await make_chat(user, "project", project_id=project.id)
    support = await make_chat(user, "support")

    assert {c.id for c in await svc.archive_cabinet_chats(cabinet.id)} == {cabinet_chat.id, other_cabinet_chat.id}
    assert project_chat.archived_at is None and support.archived_at is None
    assert [c.id for c in await svc.archive_project_chats(project.id)] == [project_chat.id]

    cabinet_chat.archived_at = other_cabinet_chat.archived_at = project_chat.archived_at = None
    mine = await svc.archive_user_project_chats(user.id, project.id, [cabinet.id])
    assert {c.id for c in mine} == {project_chat.id, cabinet_chat.id}
    assert other_cabinet_chat.archived_at is None

    cabinet_chat.archived_at = None
    assert [c.id for c in await svc.archive_user_cabinet_chats(user.id, cabinet.id)] == [cabinet_chat.id]
    assert support.archived_at is None


# --- списки ---

async def test_user_chat_list(svc, make_user, make_chat, add_message, make_cabinet):
    from datetime import datetime, timezone
    user, operator = await make_user(), await make_user("operator")
    cabinet = await make_cabinet(admin_internal_name="Главный ШУ")
    chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)
    await make_chat(user, "notes")
    archived = await make_chat(user, "support", archived_at=datetime.now(timezone.utc))
    await add_message(chat, operator, "Первое")
    await add_message(chat, operator, "Удалю", deleted_at=datetime.now(timezone.utc))

    rows = await svc.list_chats(user.id)
    only_notes = await svc.list_chats(user.id, chat_type="notes")
    archived_rows = await svc.list_chats(user.id, archived=True)

    by_id = {r.id: r for r in rows}
    assert by_id[chat.id].cabinet_name == "Главный ШУ"
    assert by_id[chat.id].unread_count == 1  # удалённое не считается
    assert by_id[chat.id].last_message_text == "Первое"  # удалённые в превью списка не попадают
    assert [r.chat_type for r in only_notes] == ["notes"]
    assert [r.id for r in archived_rows] == [archived.id]
    assert archived.id not in by_id


async def test_operator_list_hides_notes_and_shows_owner(svc, make_user, make_chat, add_message):
    user, operator = await make_user(full_name="Клиент Клиентов"), await make_user("operator")
    support = await make_chat(user, "support")
    notes = await make_chat(user, "notes")
    await add_message(support, user, "Помогите")

    rows = await svc.list_operator_chats(operator.id)
    found = await svc.list_operator_chats(operator.id, search="Клиентов")
    nothing = await svc.list_operator_chats(operator.id, search="такого нет")

    by_id = {r.id: r for r in rows}
    assert notes.id not in by_id
    assert by_id[support.id].user_name == "Клиент Клиентов" and by_id[support.id].user_phone == user.phone
    assert by_id[support.id].last_message_text == "Помогите" and by_id[support.id].unread_count == 1
    assert support.id in [r.id for r in found] and nothing == []


async def test_service_request_data_in_lists(svc, make_user, make_chat, make_cabinet, make_request):
    user, operator = await make_user(), await make_user("operator")
    cabinet = await make_cabinet()
    sr = await make_request(user, cabinet)
    chat = await make_chat(user, "service_request", cabinet_id=cabinet.id, service_request_id=sr.id)

    own = {r.id: r for r in await svc.list_chats(user.id)}[chat.id]
    detail = await svc.get_operator_chat_detail(chat.id, operator.id)

    for view in (own, detail):
        assert (view.service_request_id, view.service_request_type) == (sr.id, "repair")
        assert (view.service_request_status, view.service_request_description) == ("new", "Не включается")


async def test_operator_chat_detail(svc, make_user, make_chat, make_project, make_cabinet, add_message):
    user, operator = await make_user(full_name="Клиент"), await make_user("operator")
    project = await make_project(name="Космос")
    cabinet = await make_cabinet(project_id=project.id, admin_internal_name="ШУ-1")
    chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id, project_id=project.id)
    await add_message(chat, user, "Вопрос")

    detail = await svc.get_operator_chat_detail(chat.id, operator.id)

    assert detail.cabinet_name == "ШУ-1" and detail.project_name == "Космос"
    assert detail.user_name == "Клиент" and detail.last_message_text == "Вопрос" and detail.unread_count == 1


async def test_operator_chat_detail_hides_notes_and_unknown(svc, make_user, make_chat):
    user, operator = await make_user(), await make_user("operator")
    notes = await make_chat(user, "notes")

    with pytest.raises(NotFoundError):
        await svc.get_operator_chat_detail(notes.id, operator.id)
    with pytest.raises(NotFoundError):
        await svc.get_operator_chat_detail(999999, operator.id)


# --- вложения ---

async def test_chat_attachments_filter_by_type(svc, db_session, make_user, make_chat, add_message):
    user = await make_user()
    chat = await make_chat(user, "support")
    msg = await add_message(chat, user, "файлы")
    db_session.add_all([
        MessageAttachment(message_id=msg.id, attachment_type="image", file_url="/static/a.jpg", file_name="a.jpg",
                          file_size_bytes=10, mime_type="image/jpeg"),
        MessageAttachment(message_id=msg.id, attachment_type="file", file_url="/static/b.pdf", file_name="b.pdf",
                          file_size_bytes=20, mime_type="application/pdf"),
    ])
    await db_session.flush()

    everything = await svc.get_chat_attachments(chat.id, user.id)
    images = await svc.get_chat_attachments(chat.id, user.id, "image")

    assert len(everything) == 2 and [a.file_name for a in images] == ["a.jpg"]
    assert images[0].message_id == msg.id and images[0].created_at == msg.created_at


# --- удаление чатов ---

async def test_user_deletes_own_chat_and_files_are_cleaned(
    svc, db_session, cleanups, make_user, make_chat, add_message,
):
    user = await make_user()
    chat = await make_chat(user, "project")
    msg = await add_message(chat, user)
    db_session.add(MessageAttachment(message_id=msg.id, attachment_type="file", file_url="/static/x.pdf",
                                     file_name="x.pdf", file_size_bytes=1, mime_type="application/pdf"))
    await db_session.flush()
    chat_id = chat.id

    await svc.delete_chat(chat_id, user.id)
    db_session.expire_all()

    assert await db_session.get(Chat, chat_id) is None
    assert len(cleanups) == 1


async def test_delete_chat_restrictions(svc, cleanups, make_user, make_chat):
    user, other = await make_user(), await make_user()
    support = await make_chat(user, "support")
    project = await make_chat(user, "project")

    with pytest.raises(PermissionDeniedError):
        await svc.delete_chat(support.id, user.id)
    with pytest.raises(PermissionDeniedError):
        await svc.delete_chat(project.id, other.id)
    with pytest.raises(NotFoundError):
        await svc.delete_chat(999999, user.id)
    assert cleanups == []


async def test_operator_delete_and_clear(svc, db_session, cleanups, make_user, make_chat, add_message):
    user = await make_user()
    chat, doomed = await make_chat(user, "support"), await make_chat(user, "project")
    first, second = await add_message(chat, user, "a"), await add_message(chat, user, "b")
    doomed_id = doomed.id

    await svc.clear_chat_messages(chat.id)
    await svc.operator_delete_chat(doomed_id)
    db_session.expire_all()

    for m in (first, second):
        await db_session.refresh(m)
        assert m.deleted_at is not None and m.text is None
    assert await db_session.get(Chat, doomed_id) is None
    for call in (svc.clear_chat_messages, svc.operator_delete_chat):
        with pytest.raises(NotFoundError):
            await call(999999)


# --- бот и оператор ---

async def test_operator_takes_chat_and_returns_it_to_bot(svc, make_user, make_chat):
    user = await make_user()
    chat = await make_chat(user, "support", operator_requested=True)

    await svc.operator_take_chat(chat.id)
    assert chat.bot_active is False and chat.operator_requested is False

    chat.bot_offered_operator, chat.operator_insist_count, chat.bot_no_count = True, 3, 2
    chat.bot_down_intake_step = 1
    await svc.operator_return_to_bot(chat.id)

    assert chat.bot_active is True
    assert (chat.bot_no_count, chat.operator_insist_count, chat.bot_down_intake_step) == (0, 0, 0)
    assert chat.bot_offered_operator is False and chat.operator_requested is False
    for call in (svc.operator_take_chat, svc.operator_return_to_bot):
        with pytest.raises(NotFoundError):
            await call(999999)


# --- настройки ---

async def test_chat_settings_fall_back_from_chat_to_global_to_empty(svc, make_user, make_chat):
    user = await make_user()
    chat = await make_chat(user, "support")

    empty = await svc.get_chat_settings(user.id, chat.id)
    await svc.update_chat_settings(user.id, None, ChatSettingsIn(font_size=18))
    inherited = await svc.get_chat_settings(user.id, chat.id)
    await svc.update_chat_settings(user.id, chat.id, ChatSettingsIn(font_size=12, nick_color="#ff0000"))
    own = await svc.get_chat_settings(user.id, chat.id)
    await svc.reset_chat_settings(user.id, chat.id)
    after_reset = await svc.get_chat_settings(user.id, chat.id)

    assert empty.font_size is None
    assert inherited.font_size == 18
    assert (own.font_size, own.nick_color) == (12, "#ff0000")
    assert after_reset.font_size == 18


async def test_chat_settings_are_checked_against_chat_access(svc, make_user, make_chat):
    user, stranger = await make_user(), await make_user()
    chat = await make_chat(user, "support")

    with pytest.raises(PermissionDeniedError):
        await svc.get_chat_settings(stranger.id, chat.id)
    with pytest.raises(PermissionDeniedError):
        await svc.update_chat_settings(stranger.id, chat.id, ChatSettingsIn(font_size=10))
    with pytest.raises(PermissionDeniedError):
        await svc.reset_chat_settings(stranger.id, chat.id)


async def test_wallpaper_belongs_to_chat_owner_only(svc, make_user, make_chat):
    user, operator = await make_user(), await make_user("operator")
    chat = await make_chat(user, "support")

    out = await svc.set_wallpaper(chat.id, user.id, "/static/wall.jpg")
    cleared = await svc.set_wallpaper(chat.id, user.id, None)

    assert out.chat_id == chat.id and cleared.wallpaper_url is None
    with pytest.raises(PermissionDeniedError):
        await svc.set_wallpaper(chat.id, operator.id, "/static/x.jpg")
    with pytest.raises(NotFoundError):
        await svc.set_wallpaper(999999, user.id, None)


# --- глобальный поиск ---

async def test_global_search(svc, make_user, make_chat, make_cabinet, add_message):
    from datetime import datetime, timezone
    user = await make_user(full_name="Автор")
    cabinet = await make_cabinet(object_number="ШУ 26_205_1")
    chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)
    notes = await make_chat(user, "notes")
    hit = await add_message(chat, user, "Течёт насос на втором этаже")
    await add_message(chat, user, "Течёт, но удалено", deleted_at=datetime.now(timezone.utc))
    await add_message(notes, user, "Течёт — личная заметка")

    page = await svc.search_messages_global("течёт", 1, 20)
    nothing = await svc.search_messages_global("100%", 1, 20)  # спецсимволы LIKE экранируются

    assert [i.id for i in page.items] == [hit.id] and page.total == 1
    assert page.items[0].cabinet_object_number == "ШУ 26_205_1" and page.items[0].sender_name == "Автор"
    assert nothing.items == []
