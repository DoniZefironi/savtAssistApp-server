import secrets
import time

# EventSource/WebSocket не умеют слать заголовок Authorization, а токен в
# query-параметре светился бы в логах nginx. Поэтому вместо самого JWT в SSE
# передаётся короткоживущий тикет, полученный заранее обычным REST-вызовом
# с Bearer-токеном.
TICKET_TTL_SECONDS = 300

_tickets: dict[str, tuple[int, float]] = {}  # ticket -> (user_id, expires_at)


def issue_ticket(user_id: int) -> str:
    _cleanup()
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = (user_id, time.monotonic() + TICKET_TTL_SECONDS)
    return ticket


def consume_ticket(ticket: str) -> int | None:
    """Валиден многократно в пределах TTL, не удаляется после первой проверки.

    Важно: браузерный EventSource при любом обрыве соединения (смена сети,
    сон вкладки, короткий сбой на сервере) сам переподключается тем же URL —
    с тем же тикетом в query. Если бы тикет удалялся после первого
    использования (как было раньше), любое автопереподключение браузера
    получало бы 401 и клиент навсегда терял бы live-обновления до полной
    перезагрузки компонента. TTL уже ограничивает окно действия тикета —
    дополнительное одноразовое использование только вредит устойчивости."""
    _cleanup()
    entry = _tickets.get(ticket)
    if entry is None:
        return None
    user_id, expires_at = entry
    if expires_at < time.monotonic():
        _tickets.pop(ticket, None)
        return None
    return user_id


def _cleanup() -> None:
    now = time.monotonic()
    expired = [t for t, (_, exp) in _tickets.items() if exp < now]
    for t in expired:
        _tickets.pop(t, None)
