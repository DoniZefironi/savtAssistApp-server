"""Дашборд сотрудника: счётчики того, что ждёт решения, и лента последних
событий по всем видам заявок. Подписи в ленте (detail) проверяет также
test_admin_api_fields.py."""
from datetime import datetime, timedelta, timezone

from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.document_request import DocumentRequest
from app.models.password_reset_request import PasswordResetRequest
from app.models.phone_change_request import PhoneChangeRequest
from app.models.registration_request import RegistrationRequest
from app.models.service_request import ServiceRequest
from app.services.dashboard_service import DashboardService, _snippet

NOW = datetime.now(timezone.utc)


async def _dashboard(db_session, make_user):
    operator = await make_user("operator")
    return await DashboardService(db_session).get_dashboard(operator.id)


async def _baseline(db_session, make_user):
    return (await _dashboard(db_session, make_user)).stats


async def test_counters_count_only_what_waits_for_a_decision(
    db_session, make_user, make_project, make_cabinet, make_document, make_reclamation,
):
    before = await _baseline(db_session, make_user)
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    doc = await make_document(project_id=project.id)
    db_session.add_all([
        DocumentRequest(user_id=user.id, document_id=doc.id, doc_type="manual", status="pending"),
        DocumentRequest(user_id=user.id, document_id=doc.id, doc_type="manual", status="approved"),
        CabinetAdditionRequest(user_id=user.id, project_id=project.id, photo_url="/static/a.jpg", status="pending"),
        PhoneChangeRequest(user_id=user.id, new_phone="+375291234567", status="pending"),
        PhoneChangeRequest(user_id=user.id, new_phone="+375291234568", status="rejected"),
        PasswordResetRequest(user_id=user.id, hashed_password="x", status="pending"),
        RegistrationRequest(phone="+375291110001", hashed_password="x", full_name="Новый", user_type="individual", status="pending"),
        ServiceRequest(user_id=user.id, cabinet_id=cabinet.id, request_type="repair", is_under_warranty=False,
                       description="Не включается", status="open"),
        ServiceRequest(user_id=user.id, cabinet_id=cabinet.id, request_type="repair", is_under_warranty=False,
                       description="Починили", status="closed"),
    ])
    await make_reclamation(status="new")
    await make_reclamation(status="review")
    await make_reclamation(status="in_progress")
    await db_session.flush()

    after = await _baseline(db_session, make_user)

    assert after.pending_document_requests - before.pending_document_requests == 1
    assert after.pending_addition_requests - before.pending_addition_requests == 1
    assert after.pending_phone_change_requests - before.pending_phone_change_requests == 1
    assert after.pending_password_reset_requests - before.pending_password_reset_requests == 1
    assert after.pending_registration_requests - before.pending_registration_requests == 1
    assert after.open_service_requests - before.open_service_requests == 1
    assert after.pending_reclamations - before.pending_reclamations == 2   # новые и на рассмотрении


async def test_feed_has_every_kind_of_event_newest_first(
    db_session, make_user, make_project, make_cabinet, make_document, make_reclamation,
):
    user = await make_user(full_name="Иванов Иван")
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    doc = await make_document(project_id=project.id, doc_type="scheme")
    moments = [NOW - timedelta(minutes=n) for n in range(7)]
    db_session.add_all([
        ServiceRequest(user_id=user.id, cabinet_id=cabinet.id, request_type="repair", is_under_warranty=False,
                       description="Течёт насос", status="open", created_at=moments[0]),
        DocumentRequest(user_id=user.id, document_id=doc.id, cabinet_id=cabinet.id, doc_type="scheme",
                        status="pending", created_at=moments[1]),
        CabinetAdditionRequest(user_id=user.id, project_id=project.id, photo_url="/static/a.jpg",
                               user_comment="Шкаф у входа", status="pending", created_at=moments[2]),
        PhoneChangeRequest(user_id=user.id, new_phone="+375291234567", status="pending", created_at=moments[3]),
        PasswordResetRequest(user_id=user.id, hashed_password="x", user_comment="Забыл пароль", status="pending",
                             created_at=moments[4]),
        RegistrationRequest(phone="+375291110002", hashed_password="x", full_name="Петров Пётр",
                            user_type="organization", organization_name="ООО Ромашка", status="pending",
                            created_at=moments[6]),
    ])
    await make_reclamation(user=user, description="Не работает кнопка", created_at=moments[5])
    await db_session.flush()
    operator = await make_user("operator")

    feed = (await DashboardService(db_session).get_dashboard(operator.id)).recent_activity

    ours = [i for i in feed if i.user_full_name in ("Иванов Иван", "Петров Пётр")]
    assert [i.type for i in ours] == [
        "service", "document", "addition", "phone_change", "password_reset", "reclamation", "registration",
    ]
    by_type = {i.type: i for i in ours}
    assert by_type["service"].detail == "Течёт насос" and by_type["service"].cabinet_id == cabinet.id
    assert by_type["document"].detail == "scheme" and by_type["addition"].detail == "Шкаф у входа"
    assert by_type["phone_change"].detail == "+375291234567" and by_type["password_reset"].detail == "Забыл пароль"
    assert by_type["reclamation"].detail == "Не работает кнопка"
    assert by_type["registration"].detail == "ООО Ромашка" and by_type["registration"].user_id is None
    assert feed == sorted(feed, key=lambda i: i.created_at, reverse=True)


async def test_feed_is_capped_at_ten_events(db_session, make_user):
    user = await make_user()
    db_session.add_all([
        PhoneChangeRequest(user_id=user.id, new_phone=f"+3752912345{n:02d}", status="pending",
                           created_at=NOW - timedelta(minutes=n))
        for n in range(15)
    ])
    await db_session.flush()
    operator = await make_user("operator")

    feed = (await DashboardService(db_session).get_dashboard(operator.id)).recent_activity

    assert len(feed) == 10


async def test_registration_without_organization_shows_the_phone(db_session, make_user):
    db_session.add(RegistrationRequest(phone="+375291110003", hashed_password="x", full_name="Сидоров",
                                       user_type="individual", status="pending", created_at=NOW))
    await db_session.flush()

    operator = await make_user("operator")
    feed = (await DashboardService(db_session).get_dashboard(operator.id)).recent_activity

    assert next(i for i in feed if i.user_full_name == "Сидоров").detail == "+375291110003"


def test_snippet_collapses_whitespace_and_cuts_long_text():
    assert _snippet(None) is None and _snippet("") is None
    assert _snippet("  Не   включается\nвентилятор ") == "Не включается вентилятор"
    cut = _snippet("слово " * 40)
    assert len(cut) == 80 and cut.endswith("…")
