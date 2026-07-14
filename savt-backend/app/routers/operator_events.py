import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.constants import RoleName
from app.core.dependencies import require_role
from app.core.event_bus import event_bus
from app.core.exceptions import AuthenticationError
from app.core.stream_tickets import TICKET_TTL_SECONDS, consume_ticket, issue_ticket
from app.models.user import User

router = APIRouter(prefix="/operator/events", tags=["operator: realtime"])

# Пинг держит соединение живым сквозь nginx/браузер, пока нет реальных событий.
# Меньше proxy_read_timeout из nginx.conf, чтобы прокси не считал апстрим мёртвым.
_HEARTBEAT_SECONDS = 20

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # nginx: не буферизовать ответ (см. также proxy_buffering off в nginx.conf)
    "X-Accel-Buffering": "no",
}


class StreamTicketOut(BaseModel):
    ticket: str
    expires_in: int


# Тикет для подключения к SSE — EventSource не умеет слать Bearer-заголовок,
# поэтому сначала получаем одноразовый короткоживущий тикет обычным REST-запросом.
@router.post("/ticket", response_model=StreamTicketOut)
async def create_stream_ticket(
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
):
    ticket = issue_ticket(operator.id)
    return StreamTicketOut(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


def _authenticate(ticket: str) -> int:
    user_id = consume_ticket(ticket)
    if user_id is None:
        raise AuthenticationError("Тикет недействителен или истёк")
    return user_id


def _format_event(event: dict) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _sse_stream(request: Request, channel: str):
    queue = await event_bus.subscribe(channel)
    try:
        yield _format_event({"type": "connected"})
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                yield _format_event(event)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        await event_bus.unsubscribe(channel, queue)


# Список чатов оператора — замена поллинга GET /operator/chats
@router.get("/chats")
async def stream_operator_chats(request: Request, ticket: str = Query(...)):
    _authenticate(ticket)
    return StreamingResponse(
        _sse_stream(request, "operator_chats"),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# Открытый чат — замена поллинга GET /operator/chats/{chat_id}/messages
@router.get("/chats/{chat_id}")
async def stream_chat(request: Request, chat_id: int, ticket: str = Query(...)):
    _authenticate(ticket)
    return StreamingResponse(
        _sse_stream(request, f"chat:{chat_id}"),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
