import asyncio
import json
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core.dependencies import get_current_user
from app.core.event_bus import event_bus
from app.core.stream_access import RECHECK_SECONDS, may_stream
from app.core.stream_tickets import (
    SCOPE_USER,
    TICKET_TTL_SECONDS,
    consume_ticket,
    issue_ticket,
)
from app.models.user import User
from app.services.chat_service import check_chat_access

router = APIRouter(prefix="/user-events", tags=["realtime"])

# Пинг держит соединение живым сквозь nginx/мобильную сеть, пока нет реальных событий
_HEARTBEAT_SECONDS = 20


class StreamTicketOut(BaseModel):
    ticket: str
    expires_in: int


# Тикет для подключения к WebSocket — та же схема, что и у операторской SSE-панели
# (см. operator_events.py): сначала обычный REST-запрос с Bearer-токеном, потом
# короткоживущий тикет в query-параметре WS-подключения. Валиден многократно
# в пределах TTL - переподключение мобильного клиента тем же тикетом не должно ловить 401.
@router.post("/ticket", response_model=StreamTicketOut)
async def create_stream_ticket(current_user: User = Depends(get_current_user)):
    ticket = issue_ticket(current_user.id, SCOPE_USER)
    return StreamTicketOut(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


async def _authenticate(ticket: str) -> int | None:
    user_id = consume_ticket(ticket, SCOPE_USER)
    # Заблокированному или удалённому пользователю живой тикет поток не открывает
    if user_id is None or not await may_stream(user_id, SCOPE_USER):
        return None
    return user_id


async def _ws_stream(websocket: WebSocket, channel: str, user_id: int) -> None:
    await websocket.accept()
    queue = await event_bus.subscribe(channel)

    async def _sender() -> None:
        await websocket.send_text(json.dumps({"type": "connected"}))
        last_check = time.monotonic()
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                await websocket.send_text(json.dumps(event, ensure_ascii=False))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
            # Соединение живёт часами, а пользователя могли заблокировать: раз в минуту сверяемся с базой
            if time.monotonic() - last_check >= RECHECK_SECONDS:
                last_check = time.monotonic()
                if not await may_stream(user_id, SCOPE_USER):
                    await websocket.close(code=4403)
                    return

    sender_task = asyncio.create_task(_sender())
    try:
        while True:
            # Клиент ничего присылать не обязан - нам важен только момент разрыва
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        sender_task.cancel()
        await event_bus.unsubscribe(channel, queue)


# Список чатов пользователя - замена поллинга GET /chats
@router.websocket("/chats")
async def ws_chats(websocket: WebSocket, ticket: str):
    user_id = await _authenticate(ticket)
    if user_id is None:
        await websocket.close(code=4401)
        return
    await _ws_stream(websocket, f"user_chats:{user_id}", user_id)


# Открытый чат - замена поллинга GET /chats/{chat_id}/messages
@router.websocket("/chats/{chat_id}")
async def ws_chat(websocket: WebSocket, chat_id: int, ticket: str):
    user_id = await _authenticate(ticket)
    if user_id is None:
        await websocket.close(code=4401)
        return

    if not await check_chat_access(chat_id, user_id):
        await websocket.close(code=4403)
        return

    await _ws_stream(websocket, f"chat:{chat_id}", user_id)


# Сигнал о новых ШУ в проектах пользователя - замена поллинга GET /cabinets.
# Несёт только cabinet_id/project_id (см. publish_cabinet_created), сам список
# клиент перезапрашивает через REST - он персонален (custom_name, unread и т.п.)
@router.websocket("/cabinets")
async def ws_cabinets(websocket: WebSocket, ticket: str):
    user_id = await _authenticate(ticket)
    if user_id is None:
        await websocket.close(code=4401)
        return
    await _ws_stream(websocket, f"user_cabinets:{user_id}", user_id)


# Сигнал о новом событии телеметрии ШУ - замена поллинга GET /cabinets/{id}/telemetry,
# пока открыта карточка ШУ. Шлётся, только если в сообщении есть хоть один
# названный в карте регистр (см. TelemetryIngestService.ingest) - событие несёт
# только cabinet_id/event_id, сам список клиент перезапрашивает через REST
@router.websocket("/cabinets/{cabinet_id}/telemetry")
async def ws_cabinet_telemetry(websocket: WebSocket, cabinet_id: int, ticket: str):
    from app.services.telemetry_service import check_cabinet_telemetry_access

    user_id = await _authenticate(ticket)
    if user_id is None:
        await websocket.close(code=4401)
        return

    if not await check_cabinet_telemetry_access(cabinet_id, user_id):
        await websocket.close(code=4403)
        return

    await _ws_stream(websocket, f"cabinet_telemetry:{cabinet_id}", user_id)
