"""Билеты для потоков событий (SSE и WebSocket): короткоживущая замена JWT,
потому что EventSource не умеет слать заголовок Authorization."""
from app.core import stream_tickets
from app.core.stream_tickets import SCOPE_OPERATOR, SCOPE_USER, TICKET_TTL_SECONDS, consume_ticket, issue_ticket


def test_ticket_returns_its_owner():
    ticket = issue_ticket(42, SCOPE_USER)

    assert consume_ticket(ticket, SCOPE_USER) == 42


def test_unknown_ticket_is_rejected():
    assert consume_ticket("такого-нет", SCOPE_USER) is None
    assert consume_ticket("", SCOPE_OPERATOR) is None


def test_user_ticket_does_not_open_operator_streams_and_stays_valid():
    """Тикет обычного пользователя не должен открывать операторские каналы, где
    видны чужие чаты, но и не должен «сгорать» от чужой попытки."""
    ticket = issue_ticket(7, SCOPE_USER)

    assert consume_ticket(ticket, SCOPE_OPERATOR) is None
    assert consume_ticket(ticket, SCOPE_USER) == 7


def test_operator_ticket_does_not_open_user_streams():
    ticket = issue_ticket(7, SCOPE_OPERATOR)

    assert consume_ticket(ticket, SCOPE_USER) is None


def test_ticket_survives_browser_reconnects_within_the_ttl():
    """EventSource переподключается тем же адресом после любого обрыва — тикет
    не должен быть одноразовым."""
    ticket = issue_ticket(5, SCOPE_USER)

    assert [consume_ticket(ticket, SCOPE_USER) for _ in range(3)] == [5, 5, 5]


def test_ticket_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(stream_tickets.time, "monotonic", lambda: now[0])
    ticket = issue_ticket(9, SCOPE_USER)

    now[0] += TICKET_TTL_SECONDS - 1
    assert consume_ticket(ticket, SCOPE_USER) == 9

    now[0] += 2
    assert consume_ticket(ticket, SCOPE_USER) is None
    assert ticket not in stream_tickets._tickets  # просроченный удалён


def test_every_ticket_is_unique_and_expired_ones_are_cleaned(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(stream_tickets.time, "monotonic", lambda: now[0])
    first = issue_ticket(1, SCOPE_USER)
    second = issue_ticket(1, SCOPE_USER)
    assert first != second

    now[0] += TICKET_TTL_SECONDS + 1
    issue_ticket(2, SCOPE_USER)  # выдача чистит просроченные

    assert first not in stream_tickets._tickets and second not in stream_tickets._tickets
