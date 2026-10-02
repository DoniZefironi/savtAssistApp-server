"""Подписанные ссылки на файлы рекламации (вложения + подтверждающий документ).

Реальный баг, найден по жалобе с фронта: file_url/confirmation_file_url
сохранялись в БД УЖЕ подписанными (клиент присылает обратно то, что получил
от POST /upload/attachment) и отдавались в ответах как есть — без снятия
старой подписи и перевыпуска новой. Через сутки (STATIC_LINK_TTL_SECONDS)
подпись, выданная В МОМЕНТ ЗАГРУЗКИ ФАЙЛА, протухала для любой рекламации
старше суток, сколько её потом ни открывай — вопреки контракту "Конвенции
API: Подписанные ссылки на файлы" (README), где срок должен отсчитываться от
момента запроса, а не от момента загрузки.

Второе, более серьёзное следствие того же бага: bitrix_service резолвит эти
ссылки в файл на диске через _read_local_file, которая сама проверяет
подпись — с протухшей подписью она тихо возвращает None, и подтверждающий
документ/вложение просто не доезжает до Bitrix, без единой ошибки в логах
админа. См. test_reclamation_bitrix_fields.py для соседней темы (сборка
нативных полей) — здесь конкретно про подпись ссылок.
"""
from datetime import datetime

from app.core.signed_urls import sign_url, verify_signature
from app.schemas.reclamation import ReclamationAttachmentIn, ReclamationAttachmentOut, ReclamationDetailOut


def _minimal_detail(**overrides) -> dict:
    defaults = dict(
        id=1, status="new", warranty_classification=None,
        object_type="cabinet", cabinet_id=5, project_id=None, object_details=None,
        contract_number=None, order_number=None, ttn_number=None,
        description="Неисправность", occurrence_conditions=None, error_codes=None,
        contact_name="Иванов Иван", contact_phone="+375291234567", contact_email="test@example.com",
        customer_name=None, root_cause=None, resolution_comment=None, rejection_reason=None,
        responsible_name=None, responsible_phone=None,
        confirmation_file_url=None, confirmation_file_name=None,
        created_at=datetime(2026, 1, 1), resolved_at=None,
    )
    defaults.update(overrides)
    return defaults


def test_attachment_in_strips_signature_on_input():
    # клиент присылает ровно то, что получил от POST /upload/attachment —
    # уже подписанную ссылку; в БД должен попасть голый путь
    signed = sign_url("/static/attachments/uuid.jpg")
    assert "?md5=" in signed  # убедились, что подпись вообще стоит (секрет в .env задан)

    parsed = ReclamationAttachmentIn(file_url=signed)
    assert parsed.file_url == "/static/attachments/uuid.jpg"
    assert "?" not in parsed.file_url


def test_attachment_out_resigns_bare_url_on_output():
    out = ReclamationAttachmentOut(
        id=1, file_url="/static/attachments/uuid.jpg", file_name="photo.jpg",
        file_size_bytes=1024, mime_type="image/jpeg",
        created_at=datetime(2026, 1, 1),
    )
    dumped = out.model_dump(mode="json")
    assert "?md5=" in dumped["file_url"]
    assert verify_signature(dumped["file_url"]) is True


def test_attachment_out_resigns_even_if_stored_value_already_had_a_stale_signature():
    # ровно баг: в БД уже лежала старая, протухшая подпись (до фикса на входе)
    stale = "/static/attachments/uuid.jpg?md5=deadbeef&expires=1"  # expires=1 — 1970 год, точно протух
    out = ReclamationAttachmentOut(
        id=1, file_url=stale, file_name="photo.jpg",
        file_size_bytes=1024, mime_type="image/jpeg",
        created_at=datetime(2026, 1, 1),
    )
    dumped = out.model_dump(mode="json")
    # перевыпущена заново — старая протухшая подпись заменена рабочей
    assert dumped["file_url"] != stale
    assert verify_signature(dumped["file_url"]) is True


def test_confirmation_file_url_resigns_fresh_on_detail_out():
    out = ReclamationDetailOut(**_minimal_detail(confirmation_file_url="/static/documents/doc.pdf"))
    dumped = out.model_dump(mode="json")
    assert verify_signature(dumped["confirmation_file_url"]) is True


def test_confirmation_file_url_none_stays_none():
    out = ReclamationDetailOut(**_minimal_detail(confirmation_file_url=None))
    dumped = out.model_dump(mode="json")
    assert dumped["confirmation_file_url"] is None


def test_confirmation_file_url_in_strips_signature_on_input():
    from app.schemas.reclamation import AdminReclamationUpdateIn
    signed = sign_url("/static/documents/doc.pdf")
    parsed = AdminReclamationUpdateIn(confirmation_file_url=signed)
    assert parsed.confirmation_file_url == "/static/documents/doc.pdf"
