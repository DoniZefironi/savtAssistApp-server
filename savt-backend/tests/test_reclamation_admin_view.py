"""Админская карточка рекламации: pending_create_outbox показывает, что заявка не
доехала до Bitrix (и только тогда), и текст описания, который уходит в Bitrix
при создании карточки. Bitrix здесь не вызывается вообще.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.repositories.reclamation_outbox import ReclamationOutboxRepository
from app.services.reclamation_service import ReclamationService, _build_bitrix_description


async def _undelivered(db_session, make_reclamation, operation="create"):
    rec = await make_reclamation()
    row = await ReclamationOutboxRepository(db_session).create(
        rec.id, operation, {"description": "x"}, "CRM_FIELD_ERROR_REQUIRED",
    )
    return rec, row


# --- pending_create_outbox ---

async def test_admin_card_shows_pending_create_for_undelivered(db_session, make_reclamation):
    rec, row = await _undelivered(db_session, make_reclamation)

    card = await ReclamationService(db_session).get_admin(rec.id)

    assert card.pending_create_outbox is not None
    assert card.pending_create_outbox.id == row.id
    assert card.pending_create_outbox.last_error == "CRM_FIELD_ERROR_REQUIRED"


async def test_admin_card_has_no_pending_create_without_outbox_row(db_session, make_reclamation):
    rec = await make_reclamation()

    card = await ReclamationService(db_session).get_admin(rec.id)

    assert card.pending_create_outbox is None


async def test_admin_card_has_no_pending_create_once_delivered(db_session, make_reclamation):
    rec, _ = await _undelivered(db_session, make_reclamation)
    rec.bitrix_item_id = "123"
    await db_session.flush()

    card = await ReclamationService(db_session).get_admin(rec.id)

    assert card.pending_create_outbox is None


async def test_admin_card_has_no_pending_create_when_card_was_deleted_in_bitrix(db_session, make_reclamation):
    rec, _ = await _undelivered(db_session, make_reclamation)
    rec.bitrix_deleted_at = datetime.now(timezone.utc)
    await db_session.flush()

    card = await ReclamationService(db_session).get_admin(rec.id)

    assert card.pending_create_outbox is None


async def test_admin_card_ignores_non_create_operations(db_session, make_reclamation):
    rec, _ = await _undelivered(db_session, make_reclamation, operation="status")

    card = await ReclamationService(db_session).get_admin(rec.id)

    assert card.pending_create_outbox is None


# --- текст описания для Bitrix ---

def _rec(**overrides):
    defaults = dict(
        id=7, description="Не работает кнопка", object_type="documentation", object_details=None,
        contact_name="Иванов Иван", contact_phone="+375291234567", contact_email="i@example.com",
        customer_name=None, occurrence_conditions=None, error_codes=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_description_formats_free_form_details_as_readable_text():
    rec = _rec(object_details={"document_name": "gthjju", "code": "juh6", "page_or_section": "56"})

    text = _build_bitrix_description(rec)

    assert "Данные объекта: document_name: gthjju, code: juh6, page_or_section: 56" in text
    assert "{" not in text and "'" not in text


def test_description_skips_empty_detail_values_and_empty_block():
    assert "Данные объекта" not in _build_bitrix_description(_rec(object_details={"a": "", "b": None}))
    assert "b: 2" in _build_bitrix_description(_rec(object_details={"a": "", "b": 2}))


def test_description_does_not_duplicate_serial_number_for_cabinet_line_component():
    # у этих типов object_details уходит в нативные поля Bitrix, в текст не дублируется
    for object_type in ("cabinet", "line", "component"):
        text = _build_bitrix_description(_rec(object_type=object_type, object_details={"serial_number": "SN-1"}))
        assert "Данные объекта" not in text
        assert "SN-1" not in text


def test_description_contains_contact_and_optional_blocks():
    text = _build_bitrix_description(_rec(
        customer_name="ООО Ромашка", occurrence_conditions="после простоя", error_codes="E-12",
    ))

    assert "Контакт: Иванов Иван, +375291234567, i@example.com" in text
    assert "Заказчик: ООО Ромашка" in text
    assert "Условия проявления: после простоя" in text
    assert "Коды ошибок: E-12" in text
    assert "reclamation_id=7" in text
