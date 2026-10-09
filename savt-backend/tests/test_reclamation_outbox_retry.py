"""Повтор недоставленных операций с Bitrix (очередь ReclamationBitrixOutbox):
каждая операция при успехе уходит в Bitrix с сохранёнными данными и снимается с
очереди, при сбое получает +1 попытку, удалённая карточка отвязывает рекламацию.
Bitrix не вызывается: пишущие функции записывает фикстура mock_bitrix."""
import pytest
from sqlalchemy import select

from app.models.reclamation_bitrix_outbox import ReclamationBitrixOutbox
from app.repositories.reclamation_outbox import ReclamationOutboxRepository
from app.services import bitrix_service, reclamation_service
from app.services.reclamation_service import _retry_outbox_row, retry_bitrix_outbox


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


async def _row(db_session, rec, operation, payload=None):
    return await ReclamationOutboxRepository(db_session).create(rec.id, operation, payload or {}, "сбой сети")


async def _retry(db_session, row):
    return await _retry_outbox_row(db_session, ReclamationOutboxRepository(db_session), row)


async def _rows(db_session, rec):
    return list((await db_session.execute(
        select(ReclamationBitrixOutbox).where(ReclamationBitrixOutbox.reclamation_id == rec.id)
    )).scalars())


def _calls(mock_bitrix, name):
    return [(args, kwargs) for n, args, kwargs in mock_bitrix if n == name]


# --- создание карточки ---

async def test_create_is_retried_with_the_saved_payload(db_session, make_reclamation, mock_bitrix):
    rec = await make_reclamation()
    row = await _row(db_session, rec, "create", {
        "description": "Не работает кнопка", "deal_id": "77", "company_id": "5",
        "project_name": "Космос", "object_serial_number": "SN-1",
    })

    assert await _retry(db_session, row) is True

    assert rec.bitrix_item_id == "999999"
    [(args, _)] = _calls(mock_bitrix, "create_reclamation_item")
    assert args[:3] == ("Не работает кнопка", "77", "5") and "Космос" in args and "SN-1" in args
    assert await _rows(db_session, rec) == []


async def test_create_is_skipped_when_the_card_already_exists(db_session, make_reclamation, mock_bitrix):
    rec = await make_reclamation(bitrix_item_id="123")
    row = await _row(db_session, rec, "create", {"description": "x"})

    assert await _retry(db_session, row) is True

    assert _calls(mock_bitrix, "create_reclamation_item") == [] and rec.bitrix_item_id == "123"
    assert await _rows(db_session, rec) == []


async def test_create_without_configured_bitrix_counts_as_a_failure(db_session, make_reclamation, mock_bitrix, monkeypatch):
    async def not_configured(*args, **kwargs):
        return None

    monkeypatch.setattr(bitrix_service, "create_reclamation_item", not_configured)
    rec = await make_reclamation()
    row = await _row(db_session, rec, "create", {"description": "x"})

    assert await _retry(db_session, row) is False

    assert row.attempts == 2 and "BITRIX_WEBHOOK_URL" in row.last_error and rec.bitrix_item_id is None
    assert await _rows(db_session, rec) == [row]


# --- остальные операции ---

async def test_status_retry_aligns_our_status_with_what_was_pushed(db_session, make_reclamation, mock_bitrix, monkeypatch):
    async def pushed(*args, **kwargs):
        return "DT1176_69:SUCCESS"

    monkeypatch.setattr(bitrix_service, "update_reclamation_stage", pushed)
    rec = await make_reclamation(bitrix_item_id="500", status="review")
    row = await _row(db_session, rec, "status", {"status": "resolved", "deadline": "2026-12-01"})

    assert await _retry(db_session, row) is True

    assert rec.status == "resolved" and await _rows(db_session, rec) == []


async def test_status_retry_without_a_pushed_stage_keeps_our_status(db_session, make_reclamation, mock_bitrix):
    rec = await make_reclamation(bitrix_item_id="500", status="review")
    row = await _row(db_session, rec, "status", {"status": "resolved"})

    assert await _retry(db_session, row) is True

    [(args, _)] = _calls(mock_bitrix, "update_reclamation_stage")
    assert args[0] == "500" and args[1] == "resolved" and args[3] is None
    assert rec.status == "review"


@pytest.mark.parametrize("operation,payload,function,expected_args", [
    ("deadline", {"deadline": "2026-12-01"}, "update_reclamation_deadline", ("500", __import__("datetime").date(2026, 12, 1))),
    ("deadline", {"deadline": None}, "update_reclamation_deadline", ("500", None)),
    ("assignee", {"bitrix_user_id": 15}, "update_reclamation_assignee", ("500", 15)),
    ("warranty", {"warranty": True}, "update_reclamation_warranty", ("500", True)),
    ("comment", {"text": "Позвонил клиенту"}, "add_reclamation_comment", ("500", "Позвонил клиенту")),
])
async def test_simple_operations_are_replayed(db_session, make_reclamation, mock_bitrix, operation, payload, function, expected_args):
    rec = await make_reclamation(bitrix_item_id="500")
    row = await _row(db_session, rec, operation, payload)

    assert await _retry(db_session, row) is True

    assert _calls(mock_bitrix, function) == [(expected_args, {})]
    assert await _rows(db_session, rec) == []


@pytest.mark.parametrize("operation,payload", [
    ("status", {"status": "resolved"}),
    ("deadline", {"deadline": None}),
    ("assignee", {"bitrix_user_id": 15}),
    ("warranty", {"warranty": True}),
    ("comment", {"text": "x"}),
])
async def test_operations_wait_until_the_card_exists(db_session, make_reclamation, mock_bitrix, operation, payload):
    rec = await make_reclamation(bitrix_item_id=None)
    row = await _row(db_session, rec, operation, payload)

    assert await _retry(db_session, row) is False

    assert row.attempts == 2 and "bitrix_item_id" in row.last_error
    assert mock_bitrix == [] and await _rows(db_session, rec) == [row]


async def test_unknown_operation_is_left_alone(db_session, make_reclamation, mock_bitrix):
    """База не даёт завести такую строку (ограничение на operation), но защитная
    ветка не должна ни падать, ни удалять то, чего не поняла."""
    from types import SimpleNamespace
    rec = await make_reclamation(bitrix_item_id="500")
    row = SimpleNamespace(operation="teleport", id=1, reclamation_id=rec.id, payload={})

    assert await _retry(db_session, row) is False

    assert mock_bitrix == []


# --- сбои ---

async def test_failed_retry_counts_an_attempt_and_keeps_the_row(db_session, make_reclamation, mock_bitrix, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("Bitrix недоступен")

    monkeypatch.setattr(bitrix_service, "add_reclamation_comment", broken)
    rec = await make_reclamation(bitrix_item_id="500")
    row = await _row(db_session, rec, "comment", {"text": "x"})

    assert await _retry(db_session, row) is False
    assert await _retry(db_session, row) is False

    assert row.attempts == 3 and row.last_error == "Bitrix недоступен" and row.last_attempted_at is not None
    assert rec.bitrix_item_id == "500"


async def test_deleted_card_detaches_the_reclamation_and_clears_its_queue(db_session, make_reclamation, make_user, mock_bitrix, monkeypatch):
    async def gone(*args, **kwargs):
        raise RuntimeError("ERROR_NOT_FOUND: элемент не найден")

    monkeypatch.setattr(bitrix_service, "add_reclamation_comment", gone)
    admin = await make_user("admin")
    rec = await make_reclamation(bitrix_item_id="500")
    row = await _row(db_session, rec, "comment", {"text": "x"})
    await _row(db_session, rec, "deadline", {"deadline": None})

    assert await _retry(db_session, row) is False

    assert rec.bitrix_item_id is None and rec.bitrix_deleted_at is not None
    assert await _rows(db_session, rec) == []   # снята вся очередь рекламации, не только эта строка
    from app.models.notification import Notification
    notes = (await db_session.execute(select(Notification).where(Notification.user_id == admin.id))).scalars().all()
    assert any("отвязана от Bitrix" in (n.body or "") for n in notes)


# --- фоновый цикл ---

async def test_background_cycle_replays_every_row(db_session, make_reclamation, mock_bitrix, monkeypatch):
    monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: _SessionContext(db_session))
    first = await make_reclamation(bitrix_item_id="500")
    second = await make_reclamation(bitrix_item_id="501")
    await _row(db_session, first, "comment", {"text": "первый"})
    await _row(db_session, second, "assignee", {"bitrix_user_id": 7})

    await retry_bitrix_outbox()

    assert len(_calls(mock_bitrix, "add_reclamation_comment")) == 1
    assert len(_calls(mock_bitrix, "update_reclamation_assignee")) == 1
    assert await _rows(db_session, first) == [] and await _rows(db_session, second) == []
