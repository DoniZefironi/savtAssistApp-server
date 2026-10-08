"""Матрица прав на все маршруты приложения.

1. Эталон: tests/route_permissions.txt хранит, какая роль минимально допущена к
   каждому маршруту. Тест сверяет его с кодом — любое изменение прав (новая
   ручка без защиты, забытый require_role, расширенный доступ) роняет тест, и
   изменение приходится делать осознанно: обновить эталон командой
       python -m tests.route_permissions > tests/route_permissions.txt
   и проверить diff глазами.
2. Живая проверка: без токена — 401, роли ниже минимальной — 403. Обработчики
   при этом не выполняются (зависимость отказывает раньше), так что ничего не
   создаётся и не уходит наружу.
3. Токены: гостевой, чужого типа, просроченный, уволенного/заблокированного,
   несуществующего пользователя и токен с подделанной ролью.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt
import pytest

from app.config import settings
from app.core.dependencies import get_session
from app.core.security import create_access_token, create_guest_token
from app.main import app
from tests.route_permissions import AUTHENTICATED, PUBLIC, ROLES, USER_OR_GUEST, compute, render

SNAPSHOT = Path(__file__).parent / "route_permissions.txt"
PERMISSIONS = compute(app)

# Маршруты, которым нужна конкретная роль выше обычного пользователя
ROLE_GATED = sorted(
    (method, path, level) for (method, path), level in PERMISSIONS.items() if level in ROLES[1:]
)
AUTHENTICATED_ROUTES = sorted(
    (method, path) for (method, path), level in PERMISSIONS.items() if level == AUTHENTICATED
)
PROTECTED_ROUTES = sorted(
    (method, path) for (method, path), level in PERMISSIONS.items()
    if level in ROLES or level in (AUTHENTICATED, USER_OR_GUEST)
)


def _url(path: str) -> str:
    # параметры пути: и числовые, и строковые принимают "1"
    import re
    return re.sub(r"\{[^}]+\}", "1", path)


# --- эталон ---

def test_permissions_match_the_snapshot():
    expected = SNAPSHOT.read_text(encoding="utf-8")
    assert render(PERMISSIONS) == expected, (
        "Права маршрутов изменились. Если это намеренно — обновите эталон: "
        "python -m tests.route_permissions > tests/route_permissions.txt и проверьте diff."
    )


def test_every_route_is_covered_and_nothing_unexpected_is_public():
    public = sorted(f"{m} {p}" for (m, p), level in PERMISSIONS.items() if level == PUBLIC)
    # Вход и регистрация, публичные страницы QR, служебные и вебхуки (у вебхуков
    # и потоков событий своя защита — секрет/билет, см. test_webhooks_and_streams_have_own_guard)
    allowed_prefixes = (
        "POST /auth/", "GET /auth/register/status", "GET /add/", "GET /operator/events/",
        "POST /webhooks/", "GET /webhooks/", "GET /health", "GET /",
    )
    unexpected = [r for r in public if not r.startswith(allowed_prefixes)]
    assert unexpected == []


# --- живая проверка ---

@pytest.fixture
async def api(db_session):
    async def override():
        yield db_session

    app.dependency_overrides[get_session] = override
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def tokens(make_user):
    result = {}
    for role in ROLES:
        user = await make_user(role)
        result[role] = create_access_token(user_id=user.id, role=role)
    return result


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES, ids=[f"{m} {p}" for m, p in PROTECTED_ROUTES])
async def test_no_token_is_401(api, method, path):
    response = await api.request(method, _url(path))

    assert response.status_code == 401, f"{method} {path} без токена вернул {response.status_code}"


@pytest.mark.parametrize("method,path,level", ROLE_GATED, ids=[f"{m} {p}" for m, p, _ in ROLE_GATED])
async def test_roles_below_the_minimum_get_403(api, tokens, method, path, level):
    for role in ROLES[: ROLES.index(level)]:
        response = await api.request(method, _url(path), headers=_auth(tokens[role]))

        assert response.status_code == 403, (
            f"{method} {path}: роль {role} ниже минимальной ({level}), а ответ {response.status_code}"
        )


# --- токены ---

ADMIN_ROUTE = ("GET", "/admin/users")
USER_ROUTE = ("GET", "/auth/me")


@pytest.mark.parametrize("method,path", [ADMIN_ROUTE, USER_ROUTE])
async def test_guest_token_is_not_a_user(api, method, path):
    response = await api.request(method, _url(path), headers=_auth(create_guest_token()))

    assert response.status_code == 401


async def test_token_of_the_wrong_type_is_rejected(api, make_user):
    user = await make_user("admin")
    payload = {"sub": str(user.id), "role": "admin", "type": "refresh",
               "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp())}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")

    response = await api.get("/admin/users", headers=_auth(token))

    assert response.status_code == 401


async def test_expired_token_is_rejected(api, make_user):
    user = await make_user("admin")
    payload = {"sub": str(user.id), "role": "admin", "type": "access",
               "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")

    response = await api.get("/admin/users", headers=_auth(token))

    assert response.status_code == 401


async def test_token_signed_with_another_key_is_rejected(api, make_user):
    user = await make_user("admin")
    payload = {"sub": str(user.id), "role": "admin", "type": "access",
               "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp())}
    token = jwt.encode(payload, "чужой-ключ-подписи-длиннее-тридцати-двух-символов", algorithm="HS256")

    response = await api.get("/admin/users", headers=_auth(token))

    assert response.status_code == 401


async def test_deactivated_user_loses_access_immediately(api, make_user):
    admin = await make_user("admin")
    token = create_access_token(user_id=admin.id, role="admin")
    assert (await api.get("/auth/me", headers=_auth(token))).status_code == 200

    admin.is_active = False  # уволили или заблокировали — действующий токен больше не пускает

    assert (await api.get("/auth/me", headers=_auth(token))).status_code == 401
    assert (await api.get("/admin/users", headers=_auth(token))).status_code == 401


async def test_token_of_a_missing_user_is_rejected(api):
    token = create_access_token(user_id=987654321, role="admin")

    response = await api.get("/admin/users", headers=_auth(token))

    assert response.status_code == 401


async def test_role_claim_in_the_token_is_not_trusted(api, make_user):
    """Права берутся из базы, а не из токена: пользователь, чью роль понизили (или
    кто выписал токен с чужой ролью), не получает админских ручек."""
    plain_user = await make_user("user")
    forged = create_access_token(user_id=plain_user.id, role="superadmin")

    response = await api.get("/admin/users", headers=_auth(forged))

    assert response.status_code == 403


async def test_demoted_admin_is_denied_by_the_database_role(api, make_user, db_session):
    from app.models.role import Role
    from sqlalchemy import select

    admin = await make_user("admin")
    token = create_access_token(user_id=admin.id, role="admin")
    assert (await api.get("/admin/users", headers=_auth(token))).status_code == 200

    operator_role = (await db_session.execute(select(Role).where(Role.name == "operator"))).scalar_one()
    admin.role_id = operator_role.id  # понизили до оператора, токен старый

    assert (await api.post("/admin/users", headers=_auth(token))).status_code == 403


# --- защита вебхуков и потоков событий ---

@pytest.mark.parametrize("path", [
    "/webhooks/bitrix/deal", "/webhooks/bitrix/reclamation", "/webhooks/bitrix/reclamation-delete",
    "/webhooks/bitrix/task-comment", "/webhooks/bitrix/task-update",
])
async def test_bitrix_webhooks_reject_requests_without_a_valid_token(api, path):
    response = await api.post(path, data={"auth[application_token]": "wrong"})

    assert response.status_code == 403


async def test_telegram_webhook_rejects_wrong_secret(api, monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "right-secret")

    wrong = await api.post("/webhooks/telegram", json={}, headers={"x-telegram-bot-api-secret-token": "nope"})
    missing = await api.post("/webhooks/telegram", json={})

    assert wrong.status_code == 403 and missing.status_code == 403


async def test_telegram_webhook_is_closed_when_no_secret_is_configured(api, monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")

    response = await api.post("/webhooks/telegram", json={}, headers={"x-telegram-bot-api-secret-token": ""})

    assert response.status_code == 403


@pytest.mark.parametrize("method,path", [("POST", "/webhooks/telemetry"), ("GET", "/webhooks/telemetry/targets")])
async def test_telemetry_webhooks_reject_wrong_secret(api, monkeypatch, method, path):
    monkeypatch.setattr(settings, "telemetry_webhook_secret", "right-secret")

    # тело корректное: схему тела FastAPI проверяет раньше секрета, а секрет — до любой работы с базой
    body = {"json": {"topic": "t", "registers": {"1": 1}}} if method == "POST" else {}

    wrong = await api.request(method, path, headers={"X-Telemetry-Secret": "nope"}, **body)
    missing = await api.request(method, path, **body)

    assert wrong.status_code == 403 and missing.status_code == 403


async def test_telemetry_webhook_is_closed_when_no_secret_is_configured(api, monkeypatch):
    monkeypatch.setattr(settings, "telemetry_webhook_secret", "")

    response = await api.get("/webhooks/telemetry/targets", headers={"X-Telemetry-Secret": ""})

    assert response.status_code == 403


@pytest.mark.parametrize("path", [
    "/operator/events/chats", "/operator/events/chats/1", "/operator/events/cabinets/1/telemetry",
])
async def test_event_streams_need_a_valid_ticket(api, path):
    response = await api.get(path, params={"ticket": "не-билет"})

    assert response.status_code in (401, 403)


@pytest.mark.parametrize("method,path", [("POST", "/operator/events/ticket"), ("POST", "/user-events/ticket")])
async def test_ticket_issue_needs_authentication(api, method, path):
    assert (await api.request(method, path)).status_code == 401
