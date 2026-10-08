"""Лента активности: создание и решение каждого вида заявок попадает в журнал, а
оператор и админ видят все виды заявок (суперадмин — вообще всё)."""
import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.document_request import DocumentRequest
from app.schemas.auth import RegistrationRequestCreateIn
from app.services.audit_service import AuditLogger
from app.services.document_service import UserDocumentService
from app.services.registration_request_service import RegistrationRequestService
from app.services.user_cabinet_service import UserCabinetService


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _logged(db_session, action):
    return list((await db_session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


# --- создание заявок пишется в журнал ---

async def test_registration_request_creation_is_logged(db_session):
    result = await RegistrationRequestService(db_session).submit(RegistrationRequestCreateIn(
        phone="+375291234567", password="password8", password_confirm="password8",
        full_name="Иванов Иван", user_type="individual",
    ))

    [entry] = await _logged(db_session, "registration_request.create")
    assert entry.entity_type == "registration_request" and entry.entity_id == result.id
    assert entry.actor_id is None and entry.actor_role == "user"  # заявитель ещё не пользователь
    assert entry.payload == {"phone": "+375291234567", "user_type": "individual"}


async def test_document_access_request_is_logged(db_session, make_user, make_document):
    user = await make_user()
    doc = await make_document(requires_approval=True)

    request_id = await UserDocumentService(db_session).request_access(user.id, doc.id, "Нужен для проверки")

    [entry] = await _logged(db_session, "document_request.create")
    assert entry.entity_type == "document_request" and entry.entity_id == request_id
    assert entry.actor_id == user.id and entry.payload["document_id"] == doc.id
    assert (await db_session.get(DocumentRequest, request_id)) is not None


async def test_cabinet_addition_request_is_logged(db_session, make_user, make_project, link_user_project):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)

    request_id = await UserCabinetService(db_session).add_by_photo(user.id, project.id, "/static/p.jpg", "Табличка")

    [entry] = await _logged(db_session, "cabinet_request.create_addition")
    assert entry.entity_type == "cabinet_addition_request" and entry.entity_id == request_id
    assert entry.actor_id == user.id and entry.payload == {"project_id": project.id}
    assert (await db_session.get(CabinetAdditionRequest, request_id)) is not None


# --- кто что видит ---

REQUEST_TYPES = [
    "registration_request", "password_reset_request", "phone_change_request",
    "cabinet_addition_request", "document_request", "service_request",
]


async def _seed(db_session):
    logger = AuditLogger(db_session)
    for number, entity_type in enumerate(REQUEST_TYPES + ["reclamation", "user", "cabinet"], start=1):
        logger.log(f"{entity_type}.event", entity_type, number, None, "system", {})
    await db_session.flush()


async def _seen(api, token):
    response = await api.get("/admin/audit-logs", params={"size": 200}, headers=_auth(token))
    assert response.status_code == 200
    return {item["entity_type"] for item in response.json()["items"]}


async def test_operator_sees_every_kind_of_request_and_nothing_else(api, db_session, tokens):
    await _seed(db_session)

    seen = await _seen(api, tokens["operator"])

    assert set(REQUEST_TYPES) <= seen
    assert not {"reclamation", "user", "cabinet"} & seen


async def test_admin_sees_requests_and_reclamations(api, db_session, tokens):
    await _seed(db_session)

    seen = await _seen(api, tokens["admin"])

    assert set(REQUEST_TYPES) | {"reclamation"} <= seen
    assert not {"user", "cabinet"} & seen


async def test_superadmin_sees_everything(api, db_session, tokens):
    await _seed(db_session)

    seen = await _seen(api, tokens["superadmin"])

    assert set(REQUEST_TYPES) | {"reclamation", "user", "cabinet"} <= seen


@pytest.mark.parametrize("entity_type", REQUEST_TYPES)
async def test_operator_can_filter_by_each_request_type(api, db_session, tokens, entity_type):
    await _seed(db_session)

    response = await api.get(
        "/admin/audit-logs", params={"entity_type": entity_type}, headers=_auth(tokens["operator"]),
    )

    assert {i["entity_type"] for i in response.json()["items"]} == {entity_type}
