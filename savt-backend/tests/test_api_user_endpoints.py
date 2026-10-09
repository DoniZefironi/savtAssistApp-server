"""HTTP-уровень пользовательских ручек: вход и профиль, уведомления, чаты,
проекты и ШУ, избранное, чтение базы знаний и FAQ. Проверяется склейка
запрос → сервис → ответ: коды ответов, формы тел и что чужое недоступно.
Логика самих сервисов покрыта своими тестами. Лимиты запросов отключены (счётчик
общий на весь прогон), фоновые ответы бота и Telegram подменены."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.limiter import limiter
from app.core.security import create_access_token, hash_token
from app.models.refresh_token import RefreshToken
from app.services import chat_service, push_service
from app.services.chat_service import ChatService
from app.services.notification_service import NotificationService

PHONE_A = "+375291100001"
PHONE_B = "+375291100002"


@pytest.fixture(autouse=True)
def quiet_background(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(chat_service, "spawn", lambda coro, **kw: coro.close())

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(push_service, "send_push", noop)


@pytest.fixture
async def person(make_user):
    """Клиент с настоящим паролем и заголовком авторизации."""
    user = await make_user(password="password8", phone=PHONE_A, full_name="Иванов Иван")
    token = create_access_token(user_id=user.id, role="user")
    return SimpleNamespace(user=user, headers={"Authorization": f"Bearer {token}"})


def _auth(user, role="user"):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


# --- вход и профиль ---

async def test_guest_token_opens_public_reading(api):
    guest = (await api.post("/auth/guest")).json()["access_token"]

    assert (await api.get("/kb/categories", headers={"Authorization": f"Bearer {guest}"})).status_code == 200
    assert (await api.get("/faq/categories", headers={"Authorization": f"Bearer {guest}"})).status_code == 200
    assert (await api.get("/chats", headers={"Authorization": f"Bearer {guest}"})).status_code in (401, 403)
    assert (await api.get("/chats")).status_code in (401, 403)


async def test_login_refresh_logout_cycle(api, person, db_session):
    wrong = await api.post("/auth/login", json={"phone": PHONE_A, "password": "wrong-pass"})
    assert wrong.status_code == 401

    pair = (await api.post("/auth/login", json={"phone": PHONE_A, "password": "password8"})).json()
    assert pair["token_type"] == "bearer"
    me = await api.get("/auth/me", headers={"Authorization": f"Bearer {pair['access_token']}"})
    assert me.status_code == 200 and me.json()["phone"] == PHONE_A and me.json()["role"] == "user"

    rotated = (await api.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})).json()
    assert rotated["refresh_token"] != pair["refresh_token"]
    # сразу после ротации тот же токен ещё принимается (гонка вкладок), позже — это уже кража
    assert (await api.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})).status_code == 200
    old = (await db_session.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(pair["refresh_token"])))).scalar_one()
    old.revoked_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.flush()
    assert (await api.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})).status_code == 401
    assert (await api.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})).status_code == 401  # сессии завершены


async def test_logout_ends_the_session(api, person):
    pair = (await api.post("/auth/login", json={"phone": PHONE_A, "password": "password8"})).json()

    assert (await api.post("/auth/logout", json={"refresh_token": pair["refresh_token"]})).status_code == 204
    assert (await api.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})).status_code == 401


async def test_admin_login_by_login(api, make_user):
    await make_user("operator", phone=None, login="oper-1", password="password8")

    ok = await api.post("/auth/admin-login", json={"login": "oper-1", "password": "password8"})
    bad = await api.post("/auth/admin-login", json={"login": "oper-1", "password": "nope-nope"})

    assert ok.status_code == 200 and bad.status_code == 401


async def test_profile_edit_and_email_removal(api, person):
    patched = await api.patch("/auth/me", headers=person.headers, json={
        "full_name": "Новое Имя", "email": "new@example.by", "contact_phone": "+375291234567",
    })
    assert patched.status_code == 200
    assert (patched.json()["full_name"], patched.json()["email"]) == ("Новое Имя", "new@example.by")
    assert patched.json()["contact_phone"] == "+375291234567"

    cleared = await api.delete("/auth/me/email", headers=person.headers)
    assert cleared.status_code == 200 and cleared.json()["email"] is None
    assert (await api.patch("/auth/me", headers=person.headers, json={"email": "not-an-email"})).status_code == 422


async def test_password_change(api, person):
    body = {"password": "password8", "new_password": "newpass99", "new_password_confirm": "newpass99"}

    assert (await api.post("/auth/password-change", headers=person.headers, json={**body, "password": "wrong-old"})).status_code in (400, 401, 403)
    assert (await api.post("/auth/password-change", headers=person.headers, json={**body, "new_password_confirm": "other-999"})).status_code == 422
    assert (await api.post("/auth/password-change", headers=person.headers, json=body)).status_code == 200
    assert (await api.post("/auth/login", json={"phone": PHONE_A, "password": "newpass99"})).status_code == 200
    assert (await api.post("/auth/login", json={"phone": PHONE_A, "password": "password8"})).status_code == 401


async def test_phone_change_request_lifecycle(api, person):
    assert (await api.get("/auth/change-phone/request", headers=person.headers)).json() is None

    created = await api.post("/auth/change-phone/request", headers=person.headers,
                             json={"new_phone": PHONE_B, "user_comment": "Новая сим-карта"})
    assert created.status_code == 201 and created.json()["status"] == "pending"
    assert (await api.get("/auth/change-phone/request", headers=person.headers)).json()["id"] == created.json()["id"]
    assert (await api.post("/auth/change-phone/request", headers=person.headers, json={"new_phone": PHONE_B})).status_code == 409

    assert (await api.delete("/auth/change-phone/request", headers=person.headers)).status_code == 204
    assert (await api.get("/auth/change-phone/request", headers=person.headers)).json() is None


async def test_account_deletion_closes_the_session(api, person):
    assert (await api.delete("/auth/me", headers=person.headers)).status_code == 204

    assert (await api.get("/auth/me", headers=person.headers)).status_code in (401, 403)
    assert (await api.post("/auth/login", json={"phone": PHONE_A, "password": "password8"})).status_code == 401


# --- уведомления ---

async def test_notification_flow(api, person, db_session):
    svc = NotificationService(db_session)
    await svc.send(person.user.id, "request_status", "Первое", "а")
    await svc.send(person.user.id, "warranty_expiring", "Второе", "б")

    assert (await api.get("/notifications/unread-count", headers=person.headers)).json() == {"unread": 2}
    page = (await api.get("/notifications?type=warranty_expiring", headers=person.headers)).json()
    assert page["total"] == 1 and page["items"][0]["title"] == "Второе"

    first = (await api.get("/notifications?is_read=false", headers=person.headers)).json()["items"][0]["id"]
    assert (await api.post(f"/notifications/{first}/read", headers=person.headers)).status_code == 204
    assert (await api.post("/notifications/999999/read", headers=person.headers)).status_code == 404
    assert (await api.post("/notifications/read-all", headers=person.headers)).status_code == 204
    assert (await api.get("/notifications/unread-count", headers=person.headers)).json() == {"unread": 0}
    assert (await api.delete("/notifications", headers=person.headers)).status_code == 204
    assert (await api.get("/notifications", headers=person.headers)).json()["total"] == 0


async def test_notification_settings_mute_and_devices(api, person):
    assert (await api.get("/notifications/settings", headers=person.headers)).json()["chat_messages"] is True
    patched = await api.patch("/notifications/settings", headers=person.headers, json={"promotional": False})
    assert patched.json()["promotional"] is False

    muted = (await api.post("/notifications/mute", headers=person.headers, json={"hours": 2})).json()
    assert muted["is_muted"] is True
    assert (await api.delete("/notifications/mute", headers=person.headers)).json()["is_muted"] is False

    assert (await api.post("/device-tokens", headers=person.headers, json={"token": "tok-1", "platform": "android"})).status_code == 204
    assert (await api.post("/device-tokens", headers=person.headers, json={"token": "tok-2", "platform": "windows"})).status_code == 422
    assert (await api.delete("/device-tokens/tok-1", headers=person.headers)).status_code == 204
    assert (await api.delete("/device-tokens/tok-1", headers=person.headers)).status_code == 404


# --- чаты ---

@pytest.fixture
async def support_chat(db_session, person):
    chat = await ChatService(db_session).ensure_support_and_notes(person.user.id)
    await db_session.flush()
    return chat


async def test_chat_conversation_over_http(api, person, support_chat):
    chat_id = support_chat.id
    chats = (await api.get("/chats", headers=person.headers)).json()
    assert {c["chat_type"] for c in chats} == {"support", "notes"}
    assert [c["id"] for c in (await api.get("/chats?chat_type=support", headers=person.headers)).json()] == [chat_id]

    sent = await api.post(f"/chats/{chat_id}/messages", headers=person.headers, json={"text": "Здравствуйте"})
    assert sent.status_code == 201 and sent.json()["text"] == "Здравствуйте"
    msg_id = sent.json()["id"]
    assert [m["id"] for m in (await api.get(f"/chats/{chat_id}/messages", headers=person.headers)).json()] == [msg_id]
    assert (await api.get(f"/chats/{chat_id}/messages?search=Здравств", headers=person.headers)).json()[0]["id"] == msg_id

    edited = await api.patch(f"/chats/{chat_id}/messages/{msg_id}", headers=person.headers, json={"text": "Добрый день"})
    assert edited.json()["text"] == "Добрый день" and edited.json()["edited_at"] is not None

    assert (await api.post(f"/chats/{chat_id}/messages/{msg_id}/reactions/👍", headers=person.headers)).status_code == 204
    assert (await api.post(f"/chats/{chat_id}/messages/{msg_id}/reactions/👍", headers=person.headers)).status_code == 409
    assert (await api.delete(f"/chats/{chat_id}/messages/{msg_id}/reactions/👍", headers=person.headers)).status_code == 204

    pinned = await api.put(f"/chats/{chat_id}/pin/{msg_id}", headers=person.headers)
    assert [m["id"] for m in pinned.json()] == [msg_id]
    assert [m["id"] for m in (await api.get(f"/chats/{chat_id}/pinned", headers=person.headers)).json()] == [msg_id]
    assert (await api.delete(f"/chats/{chat_id}/pin/{msg_id}", headers=person.headers)).json() == []
    assert (await api.delete(f"/chats/{chat_id}/pin", headers=person.headers)).json() == []

    assert (await api.put(f"/chats/{chat_id}/pin-chat", headers=person.headers)).status_code == 204
    assert (await api.delete(f"/chats/{chat_id}/pin-chat", headers=person.headers)).status_code == 204
    assert (await api.post(f"/chats/{chat_id}/read", headers=person.headers)).status_code == 204
    assert (await api.get(f"/chats/{chat_id}/attachments", headers=person.headers)).json() == []

    bulk = await api.request("DELETE", f"/chats/{chat_id}/messages", headers=person.headers, json={"message_ids": [msg_id]})
    assert bulk.status_code == 200 and bulk.json()["deleted_ids"] == [msg_id]
    assert (await api.delete(f"/chats/{chat_id}", headers=person.headers)).status_code == 403  # чат поддержки не удалить


async def test_chat_settings_over_http(api, person, support_chat):
    chat_id = support_chat.id

    assert (await api.patch("/chats/settings", headers=person.headers, json={"font_size": 18})).json()["font_size"] == 18
    assert (await api.get(f"/chats/{chat_id}/settings", headers=person.headers)).json()["font_size"] == 18   # унаследовано
    own = await api.patch(f"/chats/{chat_id}/settings", headers=person.headers, json={"font_size": 12, "nick_color": "#ff0000"})
    assert (own.json()["font_size"], own.json()["nick_color"]) == (12, "#ff0000")
    assert (await api.patch(f"/chats/{chat_id}/settings", headers=person.headers, json={"nick_color": "красный"})).status_code == 422
    assert (await api.delete(f"/chats/{chat_id}/settings", headers=person.headers)).status_code == 204
    assert (await api.get(f"/chats/{chat_id}/settings", headers=person.headers)).json()["font_size"] == 18
    wall = await api.patch(f"/chats/{chat_id}/wallpaper", headers=person.headers, json={"wallpaper_url": "/static/w.jpg"})
    assert wall.status_code == 200


async def test_someone_elses_chat_is_closed(api, person, support_chat, make_user):
    stranger = await make_user()
    headers = _auth(stranger)

    assert (await api.get(f"/chats/{support_chat.id}/messages", headers=headers)).status_code == 403
    assert (await api.post(f"/chats/{support_chat.id}/messages", headers=headers, json={"text": "Привет"})).status_code == 403
    assert (await api.get("/chats/999999/messages", headers=person.headers)).status_code == 404


async def test_project_and_cabinet_chats_need_membership(api, person, db_session, make_project, make_cabinet, link_user_project):
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)

    assert (await api.get(f"/projects/{project.id}/chat", headers=person.headers)).status_code == 403
    assert (await api.get(f"/cabinets/{cabinet.id}/chat", headers=person.headers)).status_code == 403

    await link_user_project(person.user, project)
    assert (await api.get(f"/projects/{project.id}/chat", headers=person.headers)).json()["chat_type"] == "project"
    assert (await api.get(f"/cabinets/{cabinet.id}/chat", headers=person.headers)).json()["chat_type"] == "cabinet"


# --- проекты и ШУ ---

async def test_projects_over_http(api, person, make_project, make_cabinet, link_user_project):
    project = await make_project(name="Космос", unique_code="qr-http-1")
    await make_cabinet(project_id=project.id, admin_internal_name="ШУ-1")
    other = await make_project(name="Чужой")

    assert (await api.get("/projects", headers=person.headers)).json() == []
    added = await api.post("/projects/add-by-qr", headers=person.headers, json={"qr_data": "savt://project/qr-http-1"})
    assert added.status_code == 200 and added.json()["status"] == "linked"
    assert (await api.post("/projects/add-by-qr", headers=person.headers, json={"qr_data": "savt://project/qr-http-1"})).status_code == 409
    assert (await api.post("/projects/add-by-qr", headers=person.headers, json={"qr_data": "no-such"})).status_code == 404

    listed = (await api.get("/projects", headers=person.headers)).json()
    assert [(p["name"], p["cabinet_count"]) for p in listed] == [("Космос", 1)]
    assert (await api.get(f"/projects/{project.id}", headers=person.headers)).json()["cabinets"][0]["admin_internal_name"] == "ШУ-1"
    assert (await api.get(f"/projects/{other.id}", headers=person.headers)).status_code == 404

    assert (await api.post(f"/projects/{project.id}/pin", headers=person.headers)).status_code == 204
    assert (await api.get("/projects", headers=person.headers)).json()[0]["is_pinned"] is True
    assert (await api.delete(f"/projects/{project.id}/pin", headers=person.headers)).status_code == 204
    assert (await api.delete(f"/projects/{project.id}", headers=person.headers)).status_code == 204
    assert (await api.get("/projects", headers=person.headers)).json() == []


async def test_cabinets_over_http(api, person, make_cabinet, link_user_cabinet):
    cabinet = await make_cabinet(admin_internal_name="Насосная", unique_code="cab-http-1")

    added = await api.post("/cabinets/add-by-qr", headers=person.headers, json={"qr_data": "/add/cabinet/cab-http-1"})
    assert added.status_code == 200
    assert [c["cabinet_id"] for c in (await api.get("/cabinets", headers=person.headers)).json()] == [cabinet.id]

    patched = await api.patch(f"/cabinets/{cabinet.id}", headers=person.headers, json={"custom_name": "Мой шкаф"})
    assert patched.status_code == 200 and patched.json()["custom_name"] == "Мой шкаф"
    assert (await api.get(f"/cabinets/{cabinet.id}", headers=person.headers)).json()["custom_name"] == "Мой шкаф"
    assert (await api.post(f"/cabinets/{cabinet.id}/pin", headers=person.headers)).status_code == 204
    assert (await api.delete(f"/cabinets/{cabinet.id}/pin", headers=person.headers)).status_code == 204
    assert (await api.get(f"/cabinets/{cabinet.id}/telemetry", headers=person.headers)).json() == {"registers": []}

    assert (await api.delete(f"/cabinets/{cabinet.id}", headers=person.headers)).status_code == 204
    assert (await api.get(f"/cabinets/{cabinet.id}", headers=person.headers)).status_code in (403, 404)
    assert (await api.get(f"/cabinets/{cabinet.id}/telemetry", headers=person.headers)).status_code == 403


# --- документы, избранное, база знаний ---

async def test_documents_and_access_request_over_http(api, person, make_project, link_user_project, make_document):
    project = await make_project()
    await link_user_project(person.user, project)
    free = await make_document(project_id=project.id, requires_approval=False)
    locked = await make_document(project_id=project.id, requires_approval=True)

    page = (await api.get(f"/projects/{project.id}/documents", headers=person.headers)).json()
    by_id = {d["id"]: d for d in page["items"]}
    assert by_id[free.id]["file_url"] and by_id[locked.id]["file_url"] is None and by_id[locked.id]["has_access"] is False

    req = await api.post(f"/documents/{locked.id}/request-access", headers=person.headers, json={"user_message": "Нужен для монтажа"})
    assert req.status_code == 201
    assert (await api.post(f"/documents/{locked.id}/request-access", headers=person.headers, json={})).status_code == 409
    assert (await api.get(f"/documents/{locked.id}/download", headers=person.headers)).status_code == 403
    assert (await api.get(f"/projects/{project.id}/photos", headers=person.headers)).status_code == 403  # фото только сотрудникам


async def test_favorites_over_http(api, person):
    added = await api.post("/favorites", headers=person.headers, json={"entity_type": "document", "entity_id": 5})
    assert added.status_code == 201
    assert (await api.post("/favorites", headers=person.headers, json={"entity_type": "document", "entity_id": 5})).status_code == 409
    assert (await api.post("/favorites", headers=person.headers, json={"entity_type": "cabinet", "entity_id": 5})).status_code == 422
    assert (await api.get("/favorites?entity_type=document", headers=person.headers)).json()["total"] == 1
    assert (await api.delete("/favorites/document/5", headers=person.headers)).status_code == 204
    assert (await api.delete("/favorites/document/5", headers=person.headers)).status_code == 404


async def test_knowledge_base_and_faq_reading(api, person, db_session):
    from app.schemas.faq import FaqCategoryCreateIn, FaqEntryCreateIn, FaqEntryUpdateIn
    from app.schemas.kb import KbArticleCreateIn, KbCategoryCreateIn
    from app.services.faq_service import FaqCategoryService, FaqEntryService
    from app.services.kb_service import KbArticleService, KbCategoryService

    kb_cat = await KbCategoryService(db_session).create(KbCategoryCreateIn(name="Насосы"))
    article = await KbArticleService(db_session).create(KbArticleCreateIn(category_id=kb_cat.id, title="Запуск насоса", description="Шаг 1"))
    draft = await KbArticleService(db_session).create(KbArticleCreateIn(category_id=kb_cat.id, title="Черновик"))
    from app.schemas.kb import KbArticleUpdateIn
    await KbArticleService(db_session).update(draft.id, KbArticleUpdateIn(is_published=False))
    faq_cat = await FaqCategoryService(db_session).create(FaqCategoryCreateIn(name="Гарантия"))
    entry = await FaqEntryService(db_session).create(FaqEntryCreateIn(category_id=faq_cat.id, question="Как продлить?", answer="Через сервис"))
    await FaqEntryService(db_session).update(entry.id, FaqEntryUpdateIn(is_published=True))

    assert [c["name"] for c in (await api.get("/kb/categories", headers=person.headers)).json()] == ["Насосы"]
    listed = (await api.get("/kb/articles", headers=person.headers)).json()
    assert [a["id"] for a in listed["items"]] == [article.id]            # черновик клиенту не виден
    assert (await api.get(f"/kb/articles/{article.id}", headers=person.headers)).json()["description"] == "Шаг 1"
    assert (await api.get(f"/kb/articles/{draft.id}", headers=person.headers)).status_code == 404
    assert [c["name"] for c in (await api.get("/faq/categories", headers=person.headers)).json()] == ["Гарантия"]
    assert (await api.get("/faq/entries", headers=person.headers)).json()["items"][0]["question"] == "Как продлить?"
    assert (await api.get("/tags", headers=person.headers)).status_code == 200


# --- загрузка файлов ---

async def test_download_rejects_unsigned_and_foreign_paths(api, person):
    for url in ("/static/photos/x.jpg", "/etc/passwd", "/static/../../etc/passwd", ""):
        response = await api.get("/upload/download", params={"url": url}, headers=person.headers)
        assert response.status_code in (400, 403, 404, 422)
    assert (await api.get("/upload/download", params={"url": "/static/photos/x.jpg"})).status_code in (401, 403)
