"""Все входные поля имеют предел размера. Строка, список или словарь без предела в теле запроса
или форме сохранялись бы в БД любого размера, а строка длиннее колонки давала бы ошибку 500
вместо 422.

Тест обходит все маршруты (tests/input_limits.py). Новое поле без предела роняет его: добавьте
max_length (для словаря json_size_limit из app/schemas/limits.py) или, если предел осознанно не
нужен, впишите поле в EXPECTED_UNBOUNDED с причиной."""
import io

import pytest

from app.core.security import create_access_token
from app.main import app
from tests.input_limits import unbounded_input_fields

# 'Модель.поле (проблема)' -> причина, почему предела нет
EXPECTED_UNBOUNDED: dict[str, str] = {}


def test_every_input_field_has_a_size_limit():
    found = unbounded_input_fields(app)

    unexpected = {field: routes for field, routes in found.items() if field not in EXPECTED_UNBOUNDED}

    assert unexpected == {}, (
        "Поля без предела размера. Добавьте max_length (для словаря json_size_limit из "
        "app/schemas/limits.py) или впишите поле в EXPECTED_UNBOUNDED с причиной."
    )


def test_the_exception_list_has_no_stale_entries():
    found = unbounded_input_fields(app)

    assert [field for field in EXPECTED_UNBOUNDED if field not in found] == []


# --- границы: на пределе принимается, сверх него 422 ---

def _headers(user, role="user"):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


RECLAMATION = dict(
    object_type="line", object_details={"serial_number": "1"}, description="Течёт насос",
    contact_name="Иван", contact_phone="+375291112233", contact_email="ivan@example.by",
)


@pytest.mark.parametrize("path,payload", [
    ("/auth/register/start", dict(
        password="password8", password_confirm="password8", full_name="Иван", user_type="organization",
        organization_name="О" * 256)),
    ("/auth/register/request", dict(
        phone="+375291112233", password="password8", password_confirm="password8", full_name="Иван",
        user_type="organization", organization_name="О" * 256)),
    ("/auth/login", dict(phone="+" + "1" * 40, password="password8")),
    ("/auth/refresh", dict(refresh_token="x" * 513)),
    ("/auth/logout", dict(refresh_token="x" * 513)),
])
async def test_public_fields_over_the_limit_give_422_not_500(api, path, payload):
    response = await api.post(path, json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("override", [
    {"description": "а" * 5001},
    {"occurrence_conditions": "а" * 2001},
    {"error_codes": "а" * 1001},
    {"object_details": {"serial_number": "1", "notes": "а" * 2100}},
])
async def test_reclamation_text_fields_are_limited(api, make_user, override):
    user = await make_user()

    response = await api.post("/reclamations", headers=_headers(user), json={**RECLAMATION, **override})

    assert response.status_code == 422


async def test_reclamation_fields_at_the_limit_are_accepted_by_the_schema():
    from app.schemas.reclamation import ReclamationCreateIn

    schema = ReclamationCreateIn(**{
        **RECLAMATION, "description": "а" * 5000, "occurrence_conditions": "а" * 2000, "error_codes": "а" * 1000,
    })

    assert len(schema.description) == 5000


@pytest.mark.parametrize("path,files,data", [
    ("/admin/documents", {"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")}, {"project_id": "1", "title": "а" * 256}),
    ("/admin/photos", {"file": ("a.jpg", io.BytesIO(b"x"), "image/jpeg")}, {"project_id": "1", "caption": "а" * 501}),
])
async def test_admin_upload_forms_are_limited(api, make_user, path, files, data):
    admin = await make_user("admin")

    response = await api.post(path, headers=_headers(admin, "admin"), files=files, data=data)

    assert response.status_code == 422


async def test_qr_text_and_telemetry_registers_are_limited(api, make_user, monkeypatch):
    from app.config import settings

    operator = await make_user("operator")
    qr = await api.post("/qr/generate", headers=_headers(operator, "operator"), json={"data": "x" * 2001})

    monkeypatch.setattr(settings, "telemetry_webhook_secret", "secret")
    telemetry = await api.post(
        "/webhooks/telemetry", headers={"X-Telemetry-Secret": "secret"},
        json={"topic": "t", "registers": {str(i): 1 for i in range(1001)}},
    )

    assert qr.status_code == 422 and telemetry.status_code == 422
