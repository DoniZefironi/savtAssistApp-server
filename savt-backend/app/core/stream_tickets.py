import secrets
import time

# EventSource/WebSocket не умеют слать заголовок Authorization, а токен в
# query-параметре светился бы в логах nginx. Поэтому вместо самого JWT в SSE
# передаётся короткоживущий тикет, полученный заранее обычным REST-вызовом
# с Bearer-токеном.
TICKET_TTL_SECONDS = 300

# Область действия тикета — какими стримами им можно пользоваться. Хранилище
# тикетов одно на всё приложение, поэтому без области тикет, выданный любому
# пользователю на POST /user-events/ticket, открывал и операторские каналы
# (/operator/events/*), где видны чужие чаты и общий список всех чатов.
SCOPE_USER = "user"
SCOPE_OPERATOR = "operator"

_tickets: dict[str, tuple[int, str, float]] = {}  # ticket -> (user_id, scope, expires_at)


def issue_ticket(user_id: int, scope: str) -> str:
    _cleanup()
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = (user_id, scope, time.monotonic() + TICKET_TTL_SECONDS)
    return ticket


def consume_ticket(ticket: str, scope: str) -> int | None:
    """Возвращает user_id, только если тикет жив И выдан для этой области.

    Валиден многократно в пределах TTL, не удаляется после первой проверки.

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
    user_id, ticket_scope, expires_at = entry
    if expires_at < time.monotonic():
        _tickets.pop(ticket, None)
        return None
    # Тикет не удаляем: чужая область — не повод рвать тикет законному владельцу
    if ticket_scope != scope:
        return None
    return user_id


def _cleanup() -> None:
    now = time.monotonic()
    expired = [t for t, (_, _, exp) in _tickets.items() if exp < now]
    for t in expired:
        _tickets.pop(t, None)
