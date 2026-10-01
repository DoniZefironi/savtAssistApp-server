"""_retry_outbox_row — повтор недоставленной операции (п.8 ТЗ).

Реальная БД (транзакция теста), bitrix_service целиком замокан (mock_bitrix из
conftest) — ничего из этих тестов никогда не уходит в настоящий Bitrix, даже
читающие вызовы тут не нужны. Проверяем контракт: при успехе строка очереди
удаляется и нужный bitrix_service-вызов происходит с правильными аргументами,
при сбое — попытка засчитывается, а не теряется молча.
"""
from datetime import date

import pytest
from sqlalchemy import select

from app.models.reclamation import Reclamation
from app.models.reclamation_bitrix_outbox import ReclamationBitrixOutbox
from app.repositories.reclamation_outbox import ReclamationOutboxRepository
from app.services.reclamation_service import _retry_outbox_row


async def _outbox_count(db_session, reclamation_id: int) -> int:
    rows = (await db_session.execute(
        select(ReclamationBitrixOutbox).where(ReclamationBitrixOutbox.reclamation_id == reclamation_id)
    )).scalars().all()
    return len(rows)


# --- create ---

async def test_create_success_sets_bitrix_item_id_and_removes_row(db_session, make_reclamation, mock_bitrix):
    rec = await make_reclamation()
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(
        rec.id, "create",
        {"description": "Неисправность", "deal_id": "42", "company_id": "17",
         "project_name": "Проект", "object_serial_number": "SN-1",
         "contract_info": None, "component_info": None},
        "первая попытка упала",
    )
    await db_session.commit()

    ok = await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert ok is True
    assert await _outbox_count(db_session, rec.id) == 0
    await db_session.refresh(rec)
    assert rec.bitrix_item_id == "999999"  # фейковый id из mock_bitrix
    assert mock_bitrix[0] == (
        "create_reclamation_item",
        ("Неисправность", "42", "17", None, "Проект", "SN-1", None, None),
        {},
    )


async def test_create_skipped_if_bitrix_item_id_already_set(db_session, make_reclamation, mock_bitrix):
    # починили руками, пока запись висела в очереди — не должны создать дубль в Bitrix
    rec = await make_reclamation(bitrix_item_id="111")
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(rec.id, "create", {"description": "x"}, "err")
    await db_session.commit()

    ok = await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert ok is True
    assert await _outbox_count(db_session, rec.id) == 0
    assert mock_bitrix == []  # create_reclamation_item не вызывался вообще


async def test_create_failure_keeps_row_and_records_error(db_session, make_reclamation, monkeypatch):
    rec = await make_reclamation()
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(rec.id, "create", {"description": "x"}, "err")
    await db_session.commit()

    attempts_before = row.attempts  # снять ДО повтора — row мутируется in-place тем же вызовом

    async def _boom(*args, **kwargs):
        raise RuntimeError("Bitrix crm.item.add 500: сервер недоступен")
    monkeypatch.setattr("app.services.bitrix_service.create_reclamation_item", _boom)

    ok = await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert ok is False
    assert await _outbox_count(db_session, rec.id) == 1
    refreshed = await repo.get(row.id)
    assert refreshed.attempts == attempts_before + 1
    assert "500" in refreshed.last_error


# --- status: статус применяется у нас, только если Bitrix реально подвинул стадию ---

async def test_status_retry_applies_status_only_if_bitrix_pushed_stage(db_session, make_reclamation, monkeypatch):
    rec = await make_reclamation(bitrix_item_id="123", status="review")
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(rec.id, "status", {"status": "in_progress", "confirmation_file_url": None}, "err")
    await db_session.commit()

    async def _fake_update_stage(*args, **kwargs):
        return False  # Bitrix не принял смену стадии
    monkeypatch.setattr("app.services.bitrix_service.update_reclamation_stage", _fake_update_stage)

    ok = await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert ok is True  # сама отправка прошла без исключения
    await db_session.refresh(rec)
    assert rec.status == "review"  # но статус у нас не поменялся, раз Bitrix стадию не подвинул


async def test_status_retry_requires_bitrix_item_id(db_session, make_reclamation, mock_bitrix):
    rec = await make_reclamation(bitrix_item_id=None)
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(rec.id, "status", {"status": "in_progress"}, "err")
    await db_session.commit()

    ok = await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert ok is False
    assert await _outbox_count(db_session, rec.id) == 1
    assert mock_bitrix == []  # до вызова update_reclamation_stage дело не дошло


# --- warranty: НИКОГДА не передаёт stageId, см. предупреждение в коде про автозакрытие ---

async def test_warranty_retry_calls_update_with_item_id_and_value(db_session, make_reclamation, mock_bitrix):
    rec = await make_reclamation(bitrix_item_id="123")
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(rec.id, "warranty", {"warranty": True}, "err")
    await db_session.commit()

    ok = await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert ok is True
    assert mock_bitrix == [("update_reclamation_warranty", ("123", True), {})]


async def test_warranty_retry_clear_sends_none_not_skipped(db_session, make_reclamation, mock_bitrix):
    # баг 2026-09-28: раньше warranty=None вообще не отправлялся, и очистка в
    # нашей админке не долетала до Bitrix — проверяем, что None уходит как есть
    rec = await make_reclamation(bitrix_item_id="123")
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(rec.id, "warranty", {"warranty": None}, "err")
    await db_session.commit()

    await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert mock_bitrix == [("update_reclamation_warranty", ("123", None), {})]


# --- comment ---

async def test_comment_retry_calls_add_comment(db_session, make_reclamation, mock_bitrix):
    rec = await make_reclamation(bitrix_item_id="123")
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(rec.id, "comment", {"text": "Коренная причина: брак датчика"}, "err")
    await db_session.commit()

    ok = await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert ok is True
    assert mock_bitrix == [("add_reclamation_comment", ("123", "Коренная причина: брак датчика"), {})]


# --- удалённая карточка: не повторяем вечно, отвязываем рекламацию ---

async def test_not_found_marks_item_deleted_instead_of_retrying(db_session, make_reclamation, monkeypatch):
    rec = await make_reclamation(bitrix_item_id="123", status="in_progress")
    repo = ReclamationOutboxRepository(db_session)
    row = await repo.create(rec.id, "warranty", {"warranty": True}, "err")
    await db_session.commit()

    async def _not_found(*args, **kwargs):
        raise RuntimeError("Bitrix crm.item.update: NOT_FOUND")
    monkeypatch.setattr("app.services.bitrix_service.update_reclamation_warranty", _not_found)

    ok = await _retry_outbox_row(db_session, repo, row)
    await db_session.commit()

    assert ok is False
    await db_session.refresh(rec)
    assert rec.bitrix_item_id is None
    assert rec.bitrix_deleted_at is not None
    # NOT_FOUND снимает ВСЕ операции этой рекламации с очереди, не только текущую
    assert await _outbox_count(db_session, rec.id) == 0
