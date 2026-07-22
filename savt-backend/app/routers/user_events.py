import asyncio
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.core.event_bus import event_bus
from app.core.stream_tickets import TICKET_TTL_SECONDS, consume_ticket, issue_ticket
from app.models.user import User

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
    ticket = issue_ticket(current_user.id)
    return StreamTicketOut(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


async def _ws_stream(websocket: WebSocket, channel: str) -> None:
    await websocket.accept()
    queue = await event_bus.subscribe(channel)

    async def _sender() -> None:
        await websocket.send_text(json.dumps({"type": "connected"}))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                await websocket.send_text(json.dumps(event, ensure_ascii=False))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))

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
    user_id = consume_ticket(ticket)
    if user_id is None:
        await websocket.close(code=4401)
        return
    await _ws_stream(websocket, f"user_chats:{user_id}")


# Открытый чат - замена поллинга GET /chats/{chat_id}/messages
@router.websocket("/chats/{chat_id}")
async def ws_chat(
    websocket: WebSocket,
    chat_id: int,
    ticket: str,
    session: AsyncSession = Depends(get_session),
):
    user_id = consume_ticket(ticket)
    if user_id is None:
        await websocket.close(code=4401)
        return

    from app.services.chat_service import ChatService
    if not await ChatService(session).has_access(chat_id, user_id):
        await websocket.close(code=4403)
        return

    await _ws_stream(websocket, f"chat:{chat_id}")
