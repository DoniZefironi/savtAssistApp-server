"""HTTP-уровень заявок и вебхуков: регистрация и сброс пароля (подача и решение
сотрудником), смена номера, добавление ШУ по фото, сервисные заявки, рекламации,
вебхуки Telegram, Bitrix и телеметрии (секреты, фоновая обработка). Внешнее
подменено: фоновые задачи, Bitrix, push."""
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.limiter import limiter
from app.core.security import create_access_token
from app.routers import bitrix_webhooks, messenger_webhooks
from app.services import (
    notification_service, push_service, realtime_events, telemetry_service,
)

PHONE = "+375291200001"
PHONE_2 = "+375291200002"
NEW_PHONE = "+375291200099"


def _auth(user, role):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


def _silence_background_tasks(monkeypatch):
    """Фоновые задачи приложения в этих тестах не запускаются: их корутины закрываются."""
    import sys
    from app.core import background

    original = background.spawn
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and name != "app.core.background" and getattr(module, "spawn", None) is original:
            monkeypatch.setattr(module, "spawn", lambda coro, **kw: coro.close())


@pytest.fixture(autouse=True)
def quiet(monkeypatch, mock_bitrix):
    monkeypatch.setattr(limiter, "enabled", False)
    _silence_background_tasks(monkeypatch)

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(push_service, "send_push", noop)
    monkeypatch.setattr(notification_service, "send_push", noop)
    monkeypatch.setattr(realtime_events, "publish_chat_created", noop)


@pytest.fixture
async def staff(make_user):
    operator, admin = await make_user("operator", full_name="Оператор"), await make_user("admin", full_name="Админ")
    return SimpleNamespace(o=_auth(operator, "operator"), a=_auth(admin, "admin"), operator=operator, admin=admin)


# --- регистрация ---

async def test_registration_start_status_and_resend(api, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_username", "savt_bot")

    started = await api.post("/auth/register/start", json={
        "password": "password8", "password_confirm": "password8", "full_name": "Иван", "user_type": "individual",
    })
    assert started.status_code == 200 and started.json()["deep_link"].startswith("https://t.me/savt_bot?start=")
    token = started.json()["registration_token"]

    status = await api.get("/auth/register/status", params={"registration_token": token})
    assert status.status_code == 200 and status.json()["status"] == "waiting_contact"
    assert (await api.get("/auth/register/status", params={"registration_token": "нет-такого"})).status_code == 404
    assert (await api.post("/auth/register/resend", json={"registration_token": token})).status_code in (200, 400, 429)
    assert (await api.post("/auth/register/complete", json={"registration_token": token, "code": "123456"})).status_code in (400, 404)


async def test_registration_request_is_decided_by_staff(api, staff):
    form = {"phone": PHONE, "password": "password8", "password_confirm": "password8", "full_name": "Иванов Иван",
            "user_type": "individual"}
    first = await api.post("/auth/register/request", json=form)
    second = await api.post("/auth/register/request", json={**form, "phone": PHONE_2, "full_name": "Петров Пётр"})
    assert first.status_code == 201 and second.status_code == 201

    listed = await api.get("/admin/registration-requests", params={"status": "pending", "search": "Иванов"}, headers=staff.o)
    assert [r["id"] for r in listed.json()["items"]] == [first.json()["id"]]
    assert (await api.post(f"/admin/registration-requests/{first.json()['id']}/approve", headers=staff.o,
                           json={"admin_response": "Добро пожаловать"})).status_code == 204
    assert (await api.post(f"/admin/registration-requests/{second.json()['id']}/reject", headers=staff.o,
                           json={"admin_response": "Нет данных"})).status_code == 204
    assert (await api.post(f"/admin/registration-requests/{first.json()['id']}/approve", headers=staff.o, json={})).status_code == 409
    assert (await api.post("/auth/login", json={"phone": PHONE, "password": "password8"})).status_code == 200


# --- сброс пароля ---

async def test_password_reset_request_is_decided_by_staff(api, staff, make_user):
    client = await make_user(phone=PHONE, password="oldpass88")
    other = await make_user(phone=PHONE_2, password="oldpass88")
    body = {"new_password": "newpass99", "new_password_confirm": "newpass99"}

    first = await api.post("/auth/password-reset/request", json={"phone": PHONE, **body, "user_comment": "Потерял Telegram"})
    second = await api.post("/auth/password-reset/request", json={"phone": PHONE_2, **body})
    unknown = await api.post("/auth/password-reset/request", json={"phone": "+375299999999", **body})
    assert first.status_code == 201 and second.status_code == 201 and unknown.status_code == 404

    listed = (await api.get("/admin/password-reset-requests", headers=staff.o)).json()
    assert {r["user_id"] for r in listed["items"]} >= {client.id, other.id}
    assert (await api.post(f"/admin/password-reset-requests/{first.json()['id']}/approve", headers=staff.o,
                           json={"admin_response": "Проверили"})).status_code == 204
    assert (await api.post(f"/admin/password-reset-requests/{second.json()['id']}/reject", headers=staff.o,
                           json={"admin_response": "Не подтвердили"})).status_code == 204
    assert (await api.post("/auth/login", json={"phone": PHONE, "password": "newpass99"})).status_code == 200


async def test_password_reset_by_code_endpoints_do_not_leak(api, make_user):
    await make_user(phone=PHONE, password="oldpass88")

    start = await api.post("/auth/password-reset/start", json={"phone": PHONE, "channel": "telegram"})
    missing = await api.post("/auth/password-reset/start", json={"phone": "+375299999999", "channel": "telegram"})
    done = await api.post("/auth/password-reset/complete", json={
        "phone": PHONE, "code": "000000", "new_password": "newpass99", "new_password_confirm": "newpass99",
    })

    assert start.status_code == 200 and missing.status_code == 200      # ответ не выдаёт, есть ли такой номер
    assert done.status_code in (400, 404)
    assert (await api.post("/auth/password-reset/start", json={"phone": PHONE, "channel": "viber"})).status_code == 422


# --- смена номера ---

async def test_phone_change_is_decided_by_staff(api, staff, make_user):
    client = await make_user(phone=PHONE, password="password8")
    other = await make_user(phone=PHONE_2, password="password8")
    for who, new in ((client, NEW_PHONE), (other, "+375291200098")):
        assert (await api.post("/auth/change-phone/request", headers=_auth(who, "user"), json={"new_phone": new})).status_code == 201

    listed = (await api.get("/admin/phone-change-requests", params={"status": "pending"}, headers=staff.o)).json()
    by_user = {r["user_id"]: r["id"] for r in listed["items"]}
    assert (await api.post(f"/admin/phone-change-requests/{by_user[client.id]}/approve", headers=staff.o, json={})).status_code == 204
    assert (await api.post(f"/admin/phone-change-requests/{by_user[other.id]}/reject", headers=staff.o,
                           json={"admin_response": "Не подтвердили владение"})).status_code == 204
    assert (await api.post("/auth/login", json={"phone": NEW_PHONE, "password": "password8"})).status_code == 200


# --- добавление ШУ по фото ---

async def test_cabinet_addition_by_photo_is_decided_by_staff(api, staff, make_user, make_project, make_cabinet, link_user_project):
    client = await make_user()
    project = await make_project()
    await link_user_project(client, project)
    target = await make_cabinet(project_id=None, object_number="29_555")

    created = await api.post("/cabinets/add-by-photo", headers=_auth(client, "user"), json={
        "project_id": project.id, "photo_url": "/static/photos/tab.jpg", "user_comment": "Табличка на дверце",
    })
    assert created.status_code == 201

    listed = (await api.get("/admin/cabinet-requests/additions", params={"status": "pending"}, headers=staff.o)).json()
    request_id = listed["items"][0]["id"]
    assert (await api.post(f"/admin/cabinet-requests/additions/{request_id}/approve", headers=staff.o,
                           json={"cabinet_id": target.id})).status_code == 204
    again = await api.post("/cabinets/add-by-photo", headers=_auth(client, "user"), json={
        "project_id": project.id, "photo_url": "/static/photos/tab2.jpg",
    })
    assert (await api.post(f"/admin/cabinet-requests/additions/{again.json()['request_id']}/reject", headers=staff.o,
                           json={"admin_response": "Не тот шкаф"})).status_code == 204


# --- сервисные заявки и рекламации ---

async def test_service_requests_over_http(api, staff, make_user, make_project, link_user_project):
    client = await make_user()
    project = await make_project()
    await link_user_project(client, project)
    headers = _auth(client, "user")

    created = await api.post("/service-requests", headers=headers, json={
        "project_id": project.id, "request_type": "repair", "description": "Не включается вентилятор", "client_token": "t-1",
    })
    assert created.status_code == 201 and created.json()["status"] == "open"
    request_id = created.json()["id"]
    assert (await api.get("/service-requests", params={"status": "open"}, headers=headers)).json()["total"] == 1
    assert (await api.post("/service-requests", headers=headers, json={"request_type": "repair", "description": "Без привязки к чему-либо"})).status_code == 422

    admin_list = (await api.get("/admin/service-requests", params={"project_id": project.id, "search": "вентилятор"}, headers=staff.o)).json()
    assert [r["id"] for r in admin_list["items"]] == [request_id]
    changed = await api.patch(f"/admin/service-requests/{request_id}/status", headers=staff.o, json={"status": "in_progress"})
    assert changed.status_code == 200 and changed.json()["status"] == "in_progress"
    assert (await api.patch(f"/admin/service-requests/{request_id}/status", headers=staff.o, json={"status": "bogus"})).status_code == 422


async def test_reclamations_over_http(api, staff, make_user):
    client = await make_user(full_name="Заявитель")
    headers = _auth(client, "user")
    body = {
        "object_type": "line", "object_details": {"serial_number": "SN-1"}, "description": "Не работает кнопка",
        "contact_name": "Заявитель", "contact_phone": PHONE, "contact_email": "a@b.by",
    }

    created = await api.post("/reclamations", headers=headers, json=body)
    assert created.status_code == 201 and created.json()["status"] == "new"
    rec_id = created.json()["id"]
    assert (await api.post("/reclamations", headers=headers, json={**body, "object_details": None})).status_code == 400   # заводской номер обязателен

    mine = (await api.get("/reclamations", params={"status": "new"}, headers=headers)).json()
    assert [r["id"] for r in mine["items"]] == [rec_id]
    assert (await api.get(f"/reclamations/{rec_id}", headers=headers)).json()["description"] == "Не работает кнопка"
    stranger = await make_user()
    assert (await api.get(f"/reclamations/{rec_id}", headers=_auth(stranger, "user"))).status_code == 404

    listed = (await api.get("/admin/reclamations", params={"search": "кнопка", "status": "new"}, headers=staff.a)).json()
    assert [r["id"] for r in listed["items"]] == [rec_id]
    assert (await api.get(f"/admin/reclamations/{rec_id}", headers=staff.a)).json()["user_id"] == client.id
    assert (await api.get("/admin/reclamations/bitrix-outbox", headers=staff.a)).json() == []
    assert (await api.get("/admin/reclamations/bitrix-detached", headers=staff.a)).json() == []
    assert (await api.patch("/admin/reclamations/bitrix-outbox/999999", headers=staff.a, json={"payload": {}})).status_code == 404
    assert (await api.delete("/admin/reclamations/bitrix-outbox/999999", headers=staff.a)).status_code == 404
    assert (await api.delete(f"/admin/reclamations/{rec_id}", headers=staff.a)).status_code == 400   # живую удалить нельзя


# --- вебхуки ---

@pytest.fixture
def hooks(monkeypatch):
    calls = SimpleNamespace(telegram=[], bitrix=[])
    monkeypatch.setattr(settings, "bitrix_incoming_webhook_tokens", "good-token")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "tg-secret")

    async def telegram(payload):
        calls.telegram.append(payload)

    def bitrix(name):
        async def handler(form):
            calls.bitrix.append((name, form["auth[application_token]"]))
        return handler

    monkeypatch.setattr(messenger_webhooks, "handle_telegram_update", telegram)
    for route, handler in (
        ("handle_task_comment_webhook", "task-comment"), ("handle_task_update_webhook", "task-update"),
        ("handle_deal_event", "deal"), ("handle_reclamation_webhook", "reclamation"),
        ("handle_reclamation_delete_webhook", "reclamation-delete"),
    ):
        monkeypatch.setattr(bitrix_webhooks, route, bitrix(handler))
    return calls


async def test_telegram_webhook_checks_the_secret(api, hooks):
    update = {"message": {"chat": {"id": 1}, "text": "/start abc"}}

    denied = await api.post("/webhooks/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    missing = await api.post("/webhooks/telegram", json=update)
    ok = await api.post("/webhooks/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})

    assert denied.status_code == missing.status_code == 403 and ok.status_code == 204
    assert hooks.telegram == [update]


@pytest.mark.parametrize("path,name", [
    ("task-comment", "task-comment"), ("task-update", "task-update"), ("deal", "deal"),
    ("reclamation", "reclamation"), ("reclamation-delete", "reclamation-delete"),
])
async def test_bitrix_webhooks_check_the_application_token(api, hooks, path, name):
    bad = await api.post(f"/webhooks/bitrix/{path}", data={"auth[application_token]": "bad"})
    none = await api.post(f"/webhooks/bitrix/{path}", data={"x": "1"})
    good = await api.post(f"/webhooks/bitrix/{path}", data={"auth[application_token]": "good-token", "data[FIELDS][ID]": "5"})

    assert bad.status_code == none.status_code == 403 and good.status_code == 204
    assert hooks.bitrix == [(name, "good-token")]


async def test_telemetry_webhook_flow(api, make_cabinet, monkeypatch):
    monkeypatch.setattr(settings, "telemetry_webhook_secret", "tele-secret")
    cabinet = await make_cabinet(mqtt_topic="t/http", mqtt_host="broker", mqtt_port=1883, mqtt_username="u")

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(telemetry_service, "publish_telemetry_event", noop)
    secret = {"X-Telemetry-Secret": "tele-secret"}

    assert (await api.get("/webhooks/telemetry/targets")).status_code == 403
    targets = (await api.get("/webhooks/telemetry/targets", headers=secret)).json()
    assert cabinet.id in [t["cabinet_id"] for t in targets]

    message = {"topic": "t/http", "registers": {"50000": 5}}
    assert (await api.post("/webhooks/telemetry", json=message)).status_code == 403
    assert (await api.post("/webhooks/telemetry", json=message, headers=secret)).status_code == 204
    unknown = await api.post("/webhooks/telemetry", json={"topic": "no/such", "registers": {"1": 1}}, headers=secret)
    assert unknown.status_code == 404


# --- мелкие ручки ---

async def test_small_public_and_staff_endpoints(api, staff, make_user, make_project, make_cabinet):
    project = await make_project(unique_code="qr-tail")
    cabinet = await make_cabinet(project_id=project.id, unique_code="cab-tail")

    assert (await api.get("/admin/dashboard", headers=staff.o)).status_code == 200
    assert (await api.get(f"/admin/projects/{project.id}/qr", headers=staff.a)).status_code == 200
    assert (await api.get("/admin/cabinets/999999/qr", headers=staff.a)).status_code == 404
    assert cabinet.id
