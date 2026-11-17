"""Предел на число заявок от одного пользователя в минуту: защита от зациклившегося клиента,
каждая заявка уходит задачей или карточкой в Bitrix."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.config import settings
from app.core.exceptions import RateLimitError
from app.core.security import create_access_token
from app.models.reclamation import Reclamation
from app.models.service_request import ServiceRequest
from app.services.creation_rate_limit import ensure_creation_allowed

RECLAMATION = {
    "object_type": "line", "object_details": {"serial_number": "SN-1"}, "description": "Не работает кнопка",
    "contact_name": "Заявитель", "contact_phone": "+375291200001", "contact_email": "a@b.by",
}


def _headers(user, role="user"):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


@pytest.fixture
def limit_3(monkeypatch):
    monkeypatch.setattr(settings, "user_requests_per_minute", 3)


def test_default_is_ten_per_minute():
    assert type(settings).model_fields["user_requests_per_minute"].default == 10


async def test_reclamations_stop_at_the_limit_and_resume_after_a_minute(api, db_session, make_user, limit_3, mock_bitrix):
    user = await make_user()

    codes = [(await api.post("/reclamations", headers=_headers(user), json=RECLAMATION)).status_code for _ in range(5)]
    assert codes == [201, 201, 201, 429, 429]
    blocked = await api.post("/reclamations", headers=_headers(user), json=RECLAMATION)
    assert blocked.json() == {"detail": "Слишком много заявок подряд. Попробуйте через минуту"}

    # прошла минута — созданные раньше в окно не попадают
    await db_session.execute(update(Reclamation).where(Reclamation.user_id == user.id).values(
        created_at=datetime.now(timezone.utc) - timedelta(seconds=61),
    ))
    assert (await api.post("/reclamations", headers=_headers(user), json=RECLAMATION)).status_code == 201


async def test_limit_is_per_user_and_per_kind(api, make_user, make_project, link_user_project, limit_3, mock_bitrix):
    user, neighbour = await make_user(), await make_user()
    project = await make_project()
    await link_user_project(user, project)
    for _ in range(3):
        assert (await api.post("/reclamations", headers=_headers(user), json=RECLAMATION)).status_code == 201
    assert (await api.post("/reclamations", headers=_headers(user), json=RECLAMATION)).status_code == 429

    # другой пользователь и другой вид заявки считаются отдельно
    assert (await api.post("/reclamations", headers=_headers(neighbour), json=RECLAMATION)).status_code == 201
    body = {"project_id": project.id, "request_type": "repair", "description": "Не включается вентилятор"}
    codes = [(await api.post("/service-requests", headers=_headers(user), json=body)).status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]


async def test_staff_and_a_disabled_limit_are_not_restricted(db_session, make_user, monkeypatch, limit_3):
    user = await make_user()
    for _ in range(3):
        db_session.add(Reclamation(
            user_id=user.id, object_type="line", description="x", contact_name="x", contact_phone="x",
            contact_email="a@b.by",
        ))
    await db_session.flush()

    with pytest.raises(RateLimitError):
        await ensure_creation_allowed(db_session, Reclamation, user.id, "user")
    await ensure_creation_allowed(db_session, Reclamation, user.id, "operator")      # сотрудник
    await ensure_creation_allowed(db_session, ServiceRequest, user.id, "user")       # другой вид заявки
    monkeypatch.setattr(settings, "user_requests_per_minute", 0)
    await ensure_creation_allowed(db_session, Reclamation, user.id, "user")          # предел выключен
