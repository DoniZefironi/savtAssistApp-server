"""Потоки событий: SSE для панели оператора и WebSocket для мобильного приложения.
Билеты и права проверяются через HTTP, а сами потоки — напрямую вызовом обработчика
в том же цикле событий (бесконечный поток через ASGI-клиент не дочитать): так видно
и приветствие, и доставку события подписчику, и пинг, и отписку при отключении."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from app.core.event_bus import event_bus
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import create_access_token
from app.core.stream_tickets import SCOPE_OPERATOR, SCOPE_USER, issue_ticket
from app.routers import operator_events, user_events
from app.services import realtime_events


def _auth(user, role):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


async def _until(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "условие не выполнилось вовремя"
        await asyncio.sleep(0.005)


def _subscribers(channel):
    return len(event_bus._subscribers.get(channel, ()))


# --- билеты и доступ по HTTP ---

async def test_each_kind_of_user_gets_a_ticket_of_its_own_scope(api, make_user):
    client, operator = await make_user(), await make_user("operator")

    user_ticket = await api.post("/user-events/ticket", headers=_auth(client, "user"))
    operator_ticket = await api.post("/operator/events/ticket", headers=_auth(operator, "operator"))
    forbidden = await api.post("/operator/events/ticket", headers=_auth(client, "user"))

    assert user_ticket.status_code == 200 and user_ticket.json()["expires_in"] > 0
    assert operator_ticket.status_code == 200 and forbidden.status_code == 403


@pytest.mark.parametrize("path", ["/operator/events/chats", "/operator/events/chats/1", "/operator/events/cabinets/1/telemetry"])
async def test_operator_streams_reject_a_foreign_scope_ticket(api, path):
    user_ticket = issue_ticket(1, SCOPE_USER)

    assert (await api.get(path, params={"ticket": user_ticket})).status_code == 401
    assert (await api.get(path, params={"ticket": "не-билет"})).status_code == 401
    assert (await api.get(path)).status_code == 422   # без билета запрос даже не разбирается


# --- конверты событий ---

async def test_publishers_use_the_expected_channels():
    queues = {}
    for channel in ("chat:5", "operator_chats", "user_chats:9", "user_cabinets:9", "cabinet_telemetry:3"):
        queues[channel] = await event_bus.subscribe(channel)

    await realtime_events.publish_message_created(5, {"id": 1})
    await realtime_events.publish_message_updated(5, {"id": 1})
    await realtime_events.publish_message_deleted(5, 1)
    await realtime_events.publish_reaction_changed(5, 1)
    await realtime_events.publish_messages_read(5, [1, 2], 9)
    await realtime_events.publish_message_pinned(5, 1)
    await realtime_events.publish_message_unpinned(5, 1)
    await realtime_events.publish_chat_updated(5, {"id": 5, "user_id": 9})
    await realtime_events.publish_chat_created(5, {"id": 5, "user_id": 9})
    await realtime_events.publish_chat_updated(6, {"id": 6})           # без владельца — только операторам
    await realtime_events.publish_cabinet_created(3, 4, [9])
    await realtime_events.publish_telemetry_event(3, 77)

    def drain(channel):
        queue, events = queues[channel], []
        while not queue.empty():
            events.append(queue.get_nowait())
        return events

    chat_events = drain("chat:5")
    assert [e["type"] for e in chat_events] == [
        "message.created", "message.updated", "message.deleted", "message.reaction_changed",
        "message.read", "message.pinned", "message.unpinned",
    ]
    assert chat_events[4]["data"] == {"message_ids": [1, 2], "reader_id": 9}
    assert [e["type"] for e in drain("operator_chats")] == ["chat.updated", "chat.created", "chat.updated"]
    assert [e["type"] for e in drain("user_chats:9")] == ["chat.updated", "chat.created"]
    assert drain("user_cabinets:9") == [{"type": "cabinet.created", "cabinet_id": 3, "project_id": 4}]
    assert drain("cabinet_telemetry:3") == [{"type": "telemetry.created", "cabinet_id": 3, "event_id": 77}]
    for channel, queue in queues.items():
        await event_bus.unsubscribe(channel, queue)


async def test_slow_subscriber_loses_events_instead_of_blocking_others():
    slow = await event_bus.subscribe("burst")
    fast = await event_bus.subscribe("burst")

    for n in range(101):
        await event_bus.publish("burst", {"type": "tick", "n": n})
        if fast.qsize() >= 50:
            while not fast.empty():
                fast.get_nowait()

    assert slow.qsize() == 100            # переполнение не уронило публикацию
    await event_bus.unsubscribe("burst", slow)
    await event_bus.unsubscribe("burst", fast)
    assert _subscribers("burst") == 0


# --- SSE ---

class _Request:
    def __init__(self):
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


def test_sse_event_format():
    text = operator_events._format_event({"type": "message.created", "data": {"текст": "привет"}})

    assert text.startswith("event: message.created\ndata: ")
    assert "привет" in text and text.endswith("\n\n")      # кириллица не экранируется
    assert operator_events._format_event({"x": 1}).startswith("event: message\n")


async def test_sse_stream_greets_delivers_pings_and_unsubscribes(monkeypatch):
    monkeypatch.setattr(operator_events, "_HEARTBEAT_SECONDS", 0.02)
    request = _Request()
    stream = operator_events._sse_stream(request, "sse-test")

    assert "event: connected" in await stream.__anext__()
    assert _subscribers("sse-test") == 1
    await event_bus.publish("sse-test", {"type": "chat.updated", "chat_id": 1})
    assert "event: chat.updated" in await stream.__anext__()
    assert await stream.__anext__() == ": ping\n\n"          # тишина — держим соединение живым

    request.disconnected = True
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    assert _subscribers("sse-test") == 0


async def test_operator_chat_stream_checks_access_on_every_connection(monkeypatch):
    request = _Request()
    ticket = issue_ticket(5, SCOPE_OPERATOR)

    async def deny(chat_id, user_id):
        return False

    async def allow(chat_id, user_id):
        return (chat_id, user_id) == (12, 5)

    monkeypatch.setattr(operator_events, "check_chat_access", deny)
    with pytest.raises(PermissionDeniedError):
        await operator_events.stream_chat(request, 12, ticket=ticket)
    with pytest.raises(AuthenticationError):
        await operator_events.stream_chat(request, 12, ticket="не-билет")

    monkeypatch.setattr(operator_events, "check_chat_access", allow)
    response = await operator_events.stream_chat(request, 12, ticket=ticket)
    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no" and response.headers["cache-control"] == "no-cache"
    await response.body_iterator.aclose()


async def test_operator_list_and_telemetry_streams_use_their_channels():
    request = _Request()
    ticket = issue_ticket(5, SCOPE_OPERATOR)

    chats = await operator_events.stream_operator_chats(request, ticket=ticket)
    telemetry = await operator_events.stream_cabinet_telemetry(request, 8, ticket=ticket)
    assert "event: connected" in await chats.body_iterator.__anext__()
    assert "event: connected" in await telemetry.body_iterator.__anext__()

    assert _subscribers("operator_chats") >= 1 and _subscribers("cabinet_telemetry:8") == 1
    await chats.body_iterator.aclose()
    await telemetry.body_iterator.aclose()
    assert _subscribers("cabinet_telemetry:8") == 0


# --- WebSocket ---

_DISCONNECT = object()


class _FakeWebSocket:
    def __init__(self):
        self.sent, self.accepted, self.closed_with = [], False, None
        self._inbox = asyncio.Queue()

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def receive_text(self):
        item = await self._inbox.get()
        if item is _DISCONNECT:
            raise WebSocketDisconnect()
        return item

    async def close(self, code=1000):
        self.closed_with = code

    def hang_up(self):
        self._inbox.put_nowait(_DISCONNECT)


async def _run(endpoint, ws, *args, **kwargs):
    task = asyncio.create_task(endpoint(ws, *args, **kwargs))
    await asyncio.sleep(0)
    return task


async def test_user_chat_list_socket_delivers_events_until_the_client_leaves():
    ws = _FakeWebSocket()
    ticket = issue_ticket(21, SCOPE_USER)
    task = await _run(user_events.ws_chats, ws, ticket=ticket)

    await _until(lambda: ws.sent)
    assert ws.accepted and ws.sent[0] == {"type": "connected"}
    await realtime_events.publish_chat_updated(7, {"id": 7, "user_id": 21})
    await _until(lambda: len(ws.sent) == 2)
    assert ws.sent[1]["type"] == "chat.updated" and ws.sent[1]["chat_id"] == 7

    ws.hang_up()
    await asyncio.wait_for(task, 1)
    assert _subscribers("user_chats:21") == 0


async def test_socket_sends_pings_while_nothing_happens(monkeypatch):
    monkeypatch.setattr(user_events, "_HEARTBEAT_SECONDS", 0.02)
    ws = _FakeWebSocket()
    task = await _run(user_events.ws_cabinets, ws, ticket=issue_ticket(22, SCOPE_USER))

    await _until(lambda: {"type": "ping"} in ws.sent)
    await realtime_events.publish_cabinet_created(3, 4, [22])
    await _until(lambda: any(e.get("type") == "cabinet.created" for e in ws.sent))

    ws.hang_up()
    await asyncio.wait_for(task, 1)
    assert _subscribers("user_cabinets:22") == 0


@pytest.mark.parametrize("endpoint,args", [
    (user_events.ws_chats, ()),
    (user_events.ws_chat, (5,)),
    (user_events.ws_cabinets, ()),
    (user_events.ws_cabinet_telemetry, (5,)),
])
async def test_sockets_refuse_a_bad_or_foreign_ticket(endpoint, args):
    for ticket in ("не-билет", issue_ticket(1, SCOPE_OPERATOR)):
        ws = _FakeWebSocket()

        await endpoint(ws, *args, ticket=ticket)

        assert ws.closed_with == 4401 and not ws.accepted


async def test_chat_socket_checks_access_to_the_chat(monkeypatch):
    allowed = {(5, 30)}

    async def check(chat_id, user_id):
        return (chat_id, user_id) in allowed

    monkeypatch.setattr(user_events, "check_chat_access", check)
    refused = _FakeWebSocket()
    await user_events.ws_chat(refused, 5, ticket=issue_ticket(31, SCOPE_USER))
    assert refused.closed_with == 4403 and not refused.accepted

    ws = _FakeWebSocket()
    task = await _run(user_events.ws_chat, ws, 5, ticket=issue_ticket(30, SCOPE_USER))
    await _until(lambda: ws.sent)
    await realtime_events.publish_message_created(5, {"id": 99, "text": "Привет"})
    await _until(lambda: len(ws.sent) == 2)
    assert ws.sent[1]["data"]["text"] == "Привет"
    ws.hang_up()
    await asyncio.wait_for(task, 1)
    assert _subscribers("chat:5") == 0


async def test_telemetry_socket_checks_access_to_the_cabinet(monkeypatch):
    from app.services import telemetry_service

    async def check(cabinet_id, user_id):
        return user_id == 40

    monkeypatch.setattr(telemetry_service, "check_cabinet_telemetry_access", check)
    refused = _FakeWebSocket()
    await user_events.ws_cabinet_telemetry(refused, 8, ticket=issue_ticket(41, SCOPE_USER))
    assert refused.closed_with == 4403

    ws = _FakeWebSocket()
    task = await _run(user_events.ws_cabinet_telemetry, ws, 8, ticket=issue_ticket(40, SCOPE_USER))
    await _until(lambda: ws.sent)
    await realtime_events.publish_telemetry_event(8, 123)
    await _until(lambda: len(ws.sent) == 2)
    assert ws.sent[1] == {"type": "telemetry.created", "cabinet_id": 8, "event_id": 123}
    ws.hang_up()
    await asyncio.wait_for(task, 1)
    assert _subscribers("cabinet_telemetry:8") == 0
