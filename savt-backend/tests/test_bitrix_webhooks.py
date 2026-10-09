"""Вебхуки Bitrix, кроме сделок (те в test_bitrix_deals.py): комментарий к задаче
пересылается в чат заявки только с префиксом /sa, изменение задачи перечитывает
статус, события рекламаций фильтруются по типу смарт-процесса. Bitrix не
вызывается: чтение задачи, автора и сообщения подменяются."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.constants import BITRIX_USER_LOGIN
from app.models.message import Message
from app.models.service_request import ServiceRequest
from app.models.user import User
from app.services import (
    bitrix_service,
    bitrix_webhook_service as hooks,
    chat_service,
    push_service,
    reclamation_service,
    service_request_service,
)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


def _comment_form(task_id="900", message_id="7"):
    return {"data[FIELDS_AFTER][TASK_ID]": task_id, "data[FIELDS_AFTER][MESSAGE_ID]": message_id}


@pytest.fixture
def bx(db_session, monkeypatch):
    """Подмена Bitrix, сессии вебхука и realtime/push чата."""
    fake = SimpleNamespace(dialog_id="chat77", comment={"text": "/sa Приезжайте завтра", "author_id": "15"},
                           names={"15": "Иван Петров"}, calls=[])

    async def get_task_chat_id(task_id):
        fake.calls.append(("chat_id", task_id))
        return fake.dialog_id

    async def get_dialog_message(dialog_id, message_id):
        fake.calls.append(("message", dialog_id, message_id))
        return fake.comment

    async def get_user_name(user_id):
        return fake.names.get(str(user_id))

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(hooks, "AsyncSessionLocal", lambda: _SessionContext(db_session))
    monkeypatch.setattr(bitrix_service, "get_task_chat_id", get_task_chat_id)
    monkeypatch.setattr(bitrix_service, "get_dialog_message", get_dialog_message)
    monkeypatch.setattr(bitrix_service, "get_user_name", get_user_name)
    monkeypatch.setattr(push_service, "send_push", noop)
    for name in ("publish_message_created", "publish_chat_updated"):
        monkeypatch.setattr(chat_service, name, noop)
    monkeypatch.setattr(chat_service, "spawn", lambda coro, **kw: coro.close())
    return fake


@pytest.fixture
async def request_chat(db_session, make_user, make_cabinet, make_chat):
    """Заявка с задачей Bitrix 900 и её чатом."""
    user = await make_user()
    cabinet = await make_cabinet()
    sr = ServiceRequest(user_id=user.id, cabinet_id=cabinet.id, request_type="repair", is_under_warranty=False,
                        description="Не включается", status="new", bitrix_task_id="900")
    db_session.add(sr)
    await db_session.flush()
    chat = await make_chat(user, "service_request", cabinet_id=cabinet.id, service_request_id=sr.id, bot_active=False)
    return SimpleNamespace(user=user, request=sr, chat=chat)


async def _messages(db_session, chat):
    return list((await db_session.execute(
        select(Message).where(Message.chat_id == chat.id).order_by(Message.id)
    )).scalars())


# --- комментарий к задаче ---

async def test_comment_with_prefix_is_forwarded_into_the_request_chat(db_session, bx, request_chat):
    await hooks.handle_task_comment_webhook(_comment_form())

    [msg] = await _messages(db_session, request_chat.chat)
    assert msg.text == "Ответ от оператора Иван Петров: Приезжайте завтра"
    sender = await db_session.get(User, msg.sender_id)
    assert sender.login == BITRIX_USER_LOGIN
    assert ("message", "chat77", "7") in bx.calls


async def test_prefix_is_case_insensitive_and_author_is_optional(db_session, bx, request_chat):
    bx.comment = {"text": "  /SA   Звоните", "AUTHOR_ID": "999"}  # автора не нашли, ключ в верхнем регистре

    await hooks.handle_task_comment_webhook(_comment_form())

    [msg] = await _messages(db_session, request_chat.chat)
    assert msg.text == "Ответ от оператора: Звоните"


@pytest.mark.parametrize("text", [
    "Внутренняя переписка без префикса",
    "/sa",
    "/sa    ",
    "",
    None,
])
async def test_other_comments_stay_inside_bitrix(db_session, bx, request_chat, text):
    bx.comment = {"text": text, "author_id": "15"}

    await hooks.handle_task_comment_webhook(_comment_form())

    assert await _messages(db_session, request_chat.chat) == []


async def test_comment_without_a_readable_message_is_ignored(db_session, bx, request_chat):
    bx.comment = None
    await hooks.handle_task_comment_webhook(_comment_form())
    bx.dialog_id = None
    await hooks.handle_task_comment_webhook(_comment_form())

    assert await _messages(db_session, request_chat.chat) == []


@pytest.mark.parametrize("form", [{}, {"data[FIELDS_AFTER][TASK_ID]": "900"}, {"data[FIELDS_AFTER][MESSAGE_ID]": "7"}])
async def test_comment_event_without_ids_does_nothing(db_session, bx, request_chat, form):
    await hooks.handle_task_comment_webhook(form)

    assert bx.calls == [] and await _messages(db_session, request_chat.chat) == []


async def test_comment_on_an_unknown_task_or_chatless_request_is_ignored(db_session, bx, make_user, make_cabinet):
    await hooks.handle_task_comment_webhook(_comment_form(task_id="no-such-task"))

    user, cabinet = await make_user(), await make_cabinet()
    db_session.add(ServiceRequest(user_id=user.id, cabinet_id=cabinet.id, request_type="repair",
                                  is_under_warranty=False, description="Без чата", status="new", bitrix_task_id="901"))
    await db_session.flush()
    await hooks.handle_task_comment_webhook(_comment_form(task_id="901"))

    assert (await db_session.execute(select(Message))).first() is None


async def test_comment_into_an_archived_chat_does_not_break_the_webhook(db_session, bx, request_chat):
    from datetime import datetime, timezone
    request_chat.chat.archived_at = datetime.now(timezone.utc)

    await hooks.handle_task_comment_webhook(_comment_form())  # исключения быть не должно

    assert await _messages(db_session, request_chat.chat) == []


# --- изменение задачи ---

async def test_task_update_rechecks_the_status(monkeypatch):
    seen = []

    async def sync(task_id):
        seen.append(task_id)

    monkeypatch.setattr(service_request_service, "sync_single_task_status", sync)

    await hooks.handle_task_update_webhook({"data[FIELDS_AFTER][ID]": "900"})
    await hooks.handle_task_update_webhook({})

    assert seen == ["900"]


# --- рекламации ---

@pytest.fixture
def reclamations(monkeypatch):
    calls = SimpleNamespace(synced=[], deleted=[])

    async def sync(item_id):
        calls.synced.append(item_id)

    async def deleted(item_id):
        calls.deleted.append(item_id)

    monkeypatch.setattr(reclamation_service, "sync_reclamation_from_bitrix", sync)
    monkeypatch.setattr(reclamation_service, "handle_bitrix_item_deleted", deleted)
    return calls


def _item_form(entity_type, item_id="55"):
    form = {}
    if entity_type is not None:
        form["data[FIELDS][ENTITY_TYPE_ID]"] = str(entity_type)
    if item_id is not None:
        form["data[FIELDS][ID]"] = item_id
    return form


async def test_reclamation_update_is_applied_only_for_our_process(reclamations):
    ours = settings.bitrix_reclamation_entity_type_id

    await hooks.handle_reclamation_webhook(_item_form(ours))
    await hooks.handle_reclamation_webhook(_item_form(1118))   # чужой смарт-процесс портала
    await hooks.handle_reclamation_webhook(_item_form(None))
    await hooks.handle_reclamation_webhook(_item_form(ours, item_id=None))

    assert reclamations.synced == ["55"]


async def test_reclamation_delete_is_applied_only_for_our_process(reclamations):
    ours = settings.bitrix_reclamation_entity_type_id

    await hooks.handle_reclamation_delete_webhook(_item_form(ours, "56"))
    await hooks.handle_reclamation_delete_webhook(_item_form(1118, "57"))
    await hooks.handle_reclamation_delete_webhook(_item_form(ours, item_id=None))

    assert reclamations.deleted == ["56"] and reclamations.synced == []
