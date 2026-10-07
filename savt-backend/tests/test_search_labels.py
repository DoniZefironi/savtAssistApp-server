"""Поиск по подписям, которые админка показывает вместо кодов из БД
(статусы, типы, гарантия), по датам и по нескольким словам сразу.
Bitrix не вызывается.
"""
from datetime import date, datetime, timezone

from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.document_request import DocumentRequest
from app.models.password_reset_request import PasswordResetRequest
from app.models.phone_change_request import PhoneChangeRequest
from app.models.registration_request import RegistrationRequest
from app.models.service_request import ServiceRequest
from app.repositories.cabinet import CabinetRequestRepository
from app.repositories.document import DocumentRequestRepository
from app.repositories.password_reset_request import PasswordResetRequestRepository
from app.repositories.phone_change import PhoneChangeRequestRepository
from app.repositories.registration_request import RegistrationRequestRepository
from app.repositories.service_request import ServiceRequestRepository
from app.repositories.user import UserRepository
from app.services.reclamation_service import ReclamationService


async def _rec_ids(db_session, **kwargs):
    page = await ReclamationService(db_session).list_admin(None, None, None, 1, 50, **kwargs)
    return [item.id for item in page.items]


# --- рекламации: гарантия ---

async def test_reclamation_search_by_warranty_labels(db_session, make_user, make_reclamation):
    applicant = await make_user(full_name="Иванов Иван", phone="+375290001111")
    warranty = await make_reclamation(user=applicant, warranty_classification=True)
    not_warranty = await make_reclamation(user=applicant, warranty_classification=False)
    unclassified = await make_reclamation(user=applicant, warranty_classification=None)

    assert await _rec_ids(db_session, search="Гарантийный случай") == [warranty.id]
    assert await _rec_ids(db_session, search="Негарантийный") == [not_warranty.id]
    assert await _rec_ids(db_session, search="Не классифицирована") == [unclassified.id]


# --- рекламации: даты ---

async def test_reclamation_search_by_date_formats(db_session, make_user, make_reclamation):
    applicant = await make_user(full_name="Иванов Иван", phone="+375290001111")
    created = await make_reclamation(
        user=applicant, created_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),
        deadline_at=date(2026, 9, 29),
    )
    other = await make_reclamation(
        user=applicant, created_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        deadline_at=date(2026, 8, 5),
    )

    for term in ("28.09.2026", "28.09.26", "28.09", "2026-09-28"):
        assert await _rec_ids(db_session, search=term) == [created.id], term
    # срок
    assert await _rec_ids(db_session, search="29.09.2026") == [created.id]
    assert await _rec_ids(db_session, search="05.08.2026") == [other.id]


async def test_reclamation_date_uses_local_day_not_utc(db_session, make_reclamation):
    # 22:30 UTC 28 сентября — в Минске уже 29 сентября
    rec = await make_reclamation(created_at=datetime(2026, 9, 28, 22, 30, tzinfo=timezone.utc))

    assert rec.id in await _rec_ids(db_session, search="29.09.2026")
    assert rec.id not in await _rec_ids(db_session, search="28.09.2026")


async def test_invalid_date_is_just_text_not_an_error(db_session, make_reclamation):
    await make_reclamation()

    assert await _rec_ids(db_session, search="31.02.2026") == []
    assert await _rec_ids(db_session, search="99.99") == []


async def test_reclamation_multi_word_label_date_and_applicant(db_session, make_user, make_reclamation):
    boba = await make_user(full_name="BOBA BOBI BOBOV", phone="+375290002222")
    hit = await make_reclamation(
        user=boba, status="resolved", created_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),
    )
    await make_reclamation(user=boba, status="new", created_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc))

    assert await _rec_ids(db_session, search="закрыта 28.09.2026 BOBOV") == [hit.id]


# --- статусы заявок ---

async def test_registration_request_search_by_status_and_user_type_label(db_session):
    pending = RegistrationRequest(
        phone="+375291110001", hashed_password="x", full_name="Первый", user_type="individual",
    )
    approved = RegistrationRequest(
        phone="+375291110002", hashed_password="x", full_name="Второй", user_type="organization",
        status="approved",
    )
    db_session.add_all([pending, approved])
    await db_session.flush()
    repo = RegistrationRequestRepository(db_session)

    assert [r.id for r in (await repo.list_requests(search="Одобрена"))[0]] == [approved.id]
    assert [r.id for r in (await repo.list_requests(search="На рассмотрении"))[0]] == [pending.id]
    assert [r.id for r in (await repo.list_requests(search="Физическое лицо"))[0]] == [pending.id]
    assert [r.id for r in (await repo.list_requests(search="Организация Второй"))[0]] == [approved.id]


async def test_phone_change_search_by_status_and_new_phone(db_session, make_user):
    user = await make_user(full_name="Иванов Иван", phone="+375290000001")
    pending = PhoneChangeRequest(user_id=user.id, new_phone="+375291234567", status="pending")
    rejected = PhoneChangeRequest(user_id=user.id, new_phone="+375297654321", status="rejected")
    db_session.add_all([pending, rejected])
    await db_session.flush()
    repo = PhoneChangeRequestRepository(db_session)

    assert [r.id for r, _ in (await repo.list_admin(search="Отклонена"))[0]] == [rejected.id]
    assert [r.id for r, _ in (await repo.list_admin(search="1234567"))[0]] == [pending.id]
    # соседний номер не подмешивается
    assert [r.id for r, _ in (await repo.list_admin(search="+375291234567"))[0]] == [pending.id]


async def test_password_reset_search_by_name_and_status(db_session, make_user):
    a = await make_user(full_name="Сидоров Семён", phone="+375290000011")
    b = await make_user(full_name="Петров Пётр", phone="+375290000012")
    req_a = PasswordResetRequest(user_id=a.id, hashed_password="x", status="pending")
    req_b = PasswordResetRequest(user_id=b.id, hashed_password="x", status="approved")
    db_session.add_all([req_a, req_b])
    await db_session.flush()
    repo = PasswordResetRequestRepository(db_session)

    assert [r.id for r, _ in (await repo.list_admin(search="Сидоров"))[0]] == [req_a.id]
    assert [r.id for r, _ in (await repo.list_admin(search="Одобрена"))[0]] == [req_b.id]
    assert [r.id for r, _ in (await repo.list_admin(search="Пётр Одобрена"))[0]] == [req_b.id]


async def test_document_request_search_by_status_label_and_type(db_session, make_user, make_project):
    user = await make_user(full_name="Иванов Иван", phone="+375290000001")
    project = await make_project()
    pending = DocumentRequest(user_id=user.id, project_id=project.id, doc_type="passport", status="pending")
    rejected = DocumentRequest(user_id=user.id, project_id=project.id, doc_type="manual", status="rejected")
    db_session.add_all([pending, rejected])
    await db_session.flush()
    repo = DocumentRequestRepository(db_session)

    assert [r[0].id for r in (await repo.list_admin(search="Отклонена"))[0]] == [rejected.id]
    assert [r[0].id for r in (await repo.list_admin(search="passport"))[0]] == [pending.id]


async def test_cabinet_addition_request_search_by_status_label(db_session, make_user):
    user = await make_user(full_name="Иванов Иван", phone="+375290000001")
    approved = CabinetAdditionRequest(user_id=user.id, photo_url="/p/1.jpg", status="approved")
    pending = CabinetAdditionRequest(user_id=user.id, photo_url="/p/2.jpg", status="pending")
    db_session.add_all([approved, pending])
    await db_session.flush()

    items, _ = await CabinetRequestRepository(db_session).list_additions(search="Одобрена")

    assert [r[0].id for r in items] == [approved.id]


async def test_service_request_search_by_status_and_type_labels(db_session, make_user, make_project):
    user = await make_user(full_name="Иванов Иван", phone="+375290000001")
    project_id = (await make_project()).id

    def make(**kw):
        sr = ServiceRequest(user_id=user.id, project_id=project_id, is_under_warranty=False,
                            description="Описание", **kw)
        db_session.add(sr)
        return sr

    repair_open = make(request_type="repair", status="open")
    diag_closed = make(request_type="diagnostics", status="closed")
    await db_session.flush()
    repo = ServiceRequestRepository(db_session)

    assert [r[0].id for r in (await repo.list_admin(search="Закрыта"))[0]] == [diag_closed.id]
    assert [r[0].id for r in (await repo.list_admin(search="Ремонт"))[0]] == [repair_open.id]
    assert [r[0].id for r in (await repo.list_admin(search="Диагностика Закрыта"))[0]] == [diag_closed.id]
    assert (await repo.list_admin(search="Ремонт Закрыта"))[0] == []


async def test_user_search_by_user_type_label(db_session, make_user):
    org = await make_user(full_name="Иванов Иван", phone="+375290000021", user_type="organization")
    person = await make_user(full_name="Петров Пётр", phone="+375290000022", user_type="individual")
    repo = UserRepository(db_session)

    assert [u.id for u, _ in (await repo.admin_search(query="Организация"))[0]] == [org.id]
    assert [u.id for u, _ in (await repo.admin_search(query="Физическое лицо"))[0]] == [person.id]
