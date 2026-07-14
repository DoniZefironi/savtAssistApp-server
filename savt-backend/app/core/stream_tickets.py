import secrets
import time

# EventSource/WebSocket не умеют слать заголовок Authorization, а токен в
# query-параметре светился бы в логах nginx. Поэтому вместо самого JWT в SSE
# передаётся одноразовый короткоживущий тикет, полученный заранее обычным
# REST-вызовом с Bearer-токеном.
TICKET_TTL_SECONDS = 30

_tickets: dict[str, tuple[int, float]] = {}  # ticket -> (user_id, expires_at)


def issue_ticket(user_id: int) -> str:
    _cleanup()
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = (user_id, time.monotonic() + TICKET_TTL_SECONDS)
    return ticket


def consume_ticket(ticket: str) -> int | None:
    """Одноразовый — при успешной проверке тикет удаляется."""
    _cleanup()
    entry = _tickets.pop(ticket, None)
    if entry is None:
        return None
    user_id, expires_at = entry
    if expires_at < time.monotonic():
        return None
    return user_id


def _cleanup() -> None:
    now = time.monotonic()
    expired = [t for t, (_, exp) in _tickets.items() if exp < now]
    for t in expired:
        _tickets.pop(t, None)
