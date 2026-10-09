"""HTTP-уровень ручек сотрудников: пользователи, проекты, ШУ, документы и фото,
база знаний и FAQ, чаты оператора, телеметрия, QR, служебные действия с ботом.
Проверяется склейка запрос → сервис → ответ и коды ответов; права по ролям
закрыты снимком tests/route_permissions.txt, логика — тестами сервисов.
Файлы, папки на NAS, фоновая индексация и лимиты запросов отключены."""
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.limiter import limiter
from app.core.security import create_access_token
from app.routers import admin_bot, admin_documents, admin_faq, admin_kb
from app.services import chat_service, document_service, project_folder_service, push_service
from app.services.chat_service import ChatService
from app.services.upload_service import FileInfo


def _auth(user, role):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


@pytest.fixture(autouse=True)
def quiet_background(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    for module in (admin_bot, admin_faq, admin_kb, chat_service):
        if hasattr(module, "spawn"):
            monkeypatch.setattr(module, "spawn", lambda coro, **kw: coro.close())
    monkeypatch.setattr(admin_documents, "schedule_reindex_document", lambda doc_id: None)
    monkeypatch.setattr(project_folder_service, "schedule_document_mirror", lambda doc_id: None)
    monkeypatch.setattr(project_folder_service, "schedule_document_removal", lambda **kw: None)
    monkeypatch.setattr(project_folder_service, "schedule_photo_removal", lambda **kw: None)
    monkeypatch.setattr(project_folder_service, "schedule_cabinet_folder", lambda cabinet_id: None)

    async def save(file):
        return FileInfo(url=f"/static/files/{file.filename}", file_size_bytes=4, mime_type=file.content_type or "application/pdf",
                        doc_type="manual")

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(document_service, "save_attachment_with_meta", save)
    monkeypatch.setattr(push_service, "send_push", noop)


@pytest.fixture
async def staff(make_user):
    admin = await make_user("admin", full_name="Админ Анна")
    operator = await make_user("operator", full_name="Оператор Олег")
    return SimpleNamespace(
        admin=admin, operator=operator,
        a=_auth(admin, "admin"), o=_auth(operator, "operator"),
    )


# --- пользователи ---

async def test_creating_and_managing_staff_over_http(api, staff):
    created = await api.post("/admin/users/operators", headers=staff.a,
                             json={"login": "oper-9", "password": "password8", "full_name": "Новый Оператор"})
    assert created.status_code == 201 and created.json()["role"] == "operator"
    op_id = created.json()["id"]
    assert (await api.post("/admin/users/operators", headers=staff.a, json={"login": "oper-9", "password": "password8"})).status_code == 409
    assert (await api.post("/admin/users/operators", headers=staff.a, json={"login": "a b", "password": "password8"})).status_code == 422

    listed = (await api.get("/admin/operators", headers=staff.a)).json()
    assert op_id in [u["id"] for u in listed["items"]]
    assert (await api.get(f"/admin/users/{op_id}", headers=staff.a)).json()["role"] == "operator"

    assert (await api.post(f"/admin/users/{op_id}/ban", headers=staff.a, json={"reason": "Нарушение"})).status_code == 204
    assert (await api.post(f"/admin/users/{op_id}/unban", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/operators/{op_id}", headers=staff.a)).status_code == 204
    assert op_id not in [u["id"] for u in (await api.get("/admin/operators", headers=staff.a)).json()["items"]]


async def test_admin_accounts_over_http(api, make_user):
    superadmin = await make_user("superadmin")
    headers = _auth(superadmin, "superadmin")

    created = await api.post("/admin/admins", headers=headers, json={"login": "boss-1", "password": "password8"})
    assert created.status_code == 201 and created.json()["role"] == "admin"
    admin_id = created.json()["id"]
    assert (await api.get(f"/admin/admins/{admin_id}", headers=headers)).json()["role"] == "admin"
    assert admin_id in [u["id"] for u in (await api.get("/admin/admins", headers=headers)).json()["items"]]
    assert (await api.delete(f"/admin/admins/{admin_id}", headers=headers)).status_code == 204
    assert (await api.delete(f"/admin/admins/{superadmin.id}", headers=headers)).status_code == 403


async def test_client_management_over_http(api, staff, make_user):
    created = await api.post("/admin/users", headers=staff.a, json={
        "phone": "+375291234567", "password": "password8", "full_name": "Сидоров Семён", "user_type": "individual",
    })
    assert created.status_code == 201
    user_id = created.json()["id"]
    assert (await api.post("/admin/users", headers=staff.a, json={
        "phone": "+375291234567", "password": "password8", "full_name": "Дубль", "user_type": "individual",
    })).status_code == 409

    clients = (await api.get("/admin/users?search=Сидоров", headers=staff.a)).json()
    assert [u["id"] for u in clients["items"]] == [user_id]
    assert (await api.post(f"/admin/users/{user_id}/unverify", headers=staff.a)).status_code == 204
    assert (await api.post(f"/admin/users/{user_id}/verify", headers=staff.a)).status_code == 204
    assert (await api.post(f"/admin/users/{user_id}/ban", headers=staff.a, json={"reason": "Спам"})).status_code == 204
    assert (await api.get(f"/admin/users/{user_id}", headers=staff.a)).json()["is_active"] is False

    senior = await make_user("admin")
    assert (await api.post(f"/admin/users/{senior.id}/ban", headers=staff.a, json={"reason": "x"})).status_code == 403
    assert (await api.get(f"/admin/users/{senior.id}", headers=staff.a)).status_code == 404


# --- проекты и ШУ ---

async def test_project_and_cabinet_administration_over_http(api, staff, make_project, make_user, link_user_project):
    project = await make_project(name="Космос", production_number="26_100")
    member = await make_user(full_name="Участник")
    await link_user_project(member, project)

    created = await api.post("/admin/cabinets", headers=staff.a, json={
        "project_id": project.id, "type": "ШУ-18К", "object_number": "26_100-1", "admin_internal_name": "Насосная",
        "latitude": 53.9, "longitude": 27.5,
    })
    assert created.status_code == 201 and created.json()["type"] == "шу-18к"
    cabinet_id = created.json()["id"]

    assert [c["id"] for c in (await api.get(f"/admin/cabinets?search=26_100-1", headers=staff.a)).json()["items"]] == [cabinet_id]
    assert cabinet_id in [p["id"] for p in (await api.get("/admin/cabinets/geo", headers=staff.a)).json()]
    assert (await api.patch(f"/admin/cabinets/{cabinet_id}", headers=staff.a, json={"description": "Три насоса"})).json()["description"] == "Три насоса"
    assert (await api.get(f"/admin/cabinets/{cabinet_id}/users", headers=staff.a)).status_code == 200
    assert (await api.put(f"/admin/cabinets/{cabinet_id}/tags", headers=staff.a, json={"tag_ids": []})).status_code == 204
    assert (await api.get(f"/admin/cabinets/{cabinet_id}/qr", headers=staff.o)).headers["content-type"] == "image/png"

    card = (await api.get(f"/admin/projects/{project.id}", headers=staff.o)).json()
    assert [c["id"] for c in card["cabinets"]] == [cabinet_id]
    assert [u["user_id"] for u in (await api.get(f"/admin/projects/{project.id}/users", headers=staff.o)).json()] == [member.id]
    assert (await api.get(f"/admin/projects/{project.id}/qr", headers=staff.o)).headers["content-type"] == "image/png"
    assert (await api.get("/admin/projects?search=Космос&year=2026", headers=staff.o)).json()["total"] == 1

    ends = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
    patched = await api.patch(f"/admin/projects/{project.id}", headers=staff.a, json={"warranty_ends_at": ends})
    assert patched.status_code == 200 and patched.json()["warranty_status"] in ("active", "expiring_soon")

    removed = await api.request("DELETE", f"/admin/projects/{project.id}/users/{member.id}", headers=staff.a, json={"reason": "Уволен"})
    assert removed.status_code == 204
    assert (await api.get(f"/admin/projects/{project.id}/users", headers=staff.o)).json() == []

    assert (await api.delete(f"/admin/cabinets/{cabinet_id}", headers=staff.a)).status_code == 204
    assert (await api.get(f"/admin/projects/{project.id}", headers=staff.a)).json()["cabinets"] == []
    assert (await api.delete(f"/admin/projects/{project.id}", headers=staff.a)).status_code == 204
    assert (await api.get(f"/admin/projects/{project.id}", headers=staff.a)).status_code == 404


async def test_cabinet_can_be_moved_between_projects(api, staff, db_session, make_project, make_cabinet):
    first, second = await make_project(), await make_project()
    cabinet = await make_cabinet(project_id=first.id)

    moved = await api.patch(f"/admin/cabinets/{cabinet.id}/project", headers=staff.a, json={"project_id": second.id})
    assert moved.status_code == 204
    await db_session.refresh(cabinet)  # в бою следующий запрос идёт в новой сессии; здесь она общая
    after = await api.get(f"/admin/cabinets/{cabinet.id}", headers=staff.a)
    assert after.status_code == 200, after.text
    assert after.json()["project_id"] == second.id
    assert (await api.patch(f"/admin/cabinets/{cabinet.id}/project", headers=staff.a, json={"project_id": 999999})).status_code == 404
    assert (await api.get("/admin/cabinets/999999", headers=staff.a)).status_code == 404


async def test_project_code_decoding_and_custom_qr(api, staff):
    assert (await api.post("/admin/projects/decode-code", headers=staff.a, json={"code": "garbage"})).status_code == 400
    qr = await api.post("/qr/generate", headers=staff.o, json={"data": "https://helper.savt.by"})
    assert qr.status_code == 200 and qr.content.startswith(b"\x89PNG")
    assert (await api.get("/admin/projects/999999/qr", headers=staff.o)).status_code == 404


# --- документы и фото ---

async def test_document_administration_over_http(api, staff, make_project):
    project = await make_project()
    pdf = ("Паспорт.pdf", io.BytesIO(b"data"), "application/pdf")

    created = await api.post("/admin/documents", headers=staff.a, files={"file": pdf},
                             data={"project_id": str(project.id), "requires_approval": "true"})
    assert created.status_code == 201 and created.json()["requires_approval"] is True
    doc_id = created.json()["id"]
    bad = await api.post("/admin/documents", headers=staff.a, files={"file": pdf}, data={})
    assert bad.status_code == 422                                    # не указано, к чему документ
    both = await api.post("/admin/documents", headers=staff.a, files={"file": pdf}, data={"project_id": "1", "cabinet_id": "1"})
    assert both.status_code == 422

    listed = (await api.get(f"/admin/documents?project_id={project.id}", headers=staff.a)).json()
    assert [d["id"] for d in listed["items"]] == [doc_id]
    assert (await api.patch(f"/admin/documents/{doc_id}", headers=staff.a, json={"requires_approval": False})).json()["requires_approval"] is False
    assert (await api.put(f"/admin/documents/{doc_id}/tags", headers=staff.a, json={"tag_ids": []})).status_code == 204
    assert (await api.delete(f"/admin/documents/{doc_id}", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/documents/{doc_id}", headers=staff.a)).status_code == 404


async def test_photo_administration_over_http(api, staff, make_cabinet):
    cabinet = await make_cabinet()
    jpg = ("щит.jpg", io.BytesIO(b"data"), "image/jpeg")

    created = await api.post("/admin/photos", headers=staff.a, files={"file": jpg},
                             data={"cabinet_id": str(cabinet.id), "caption": " Вид спереди ", "sort_order": "3"})
    assert created.status_code == 201 and (created.json()["caption"], created.json()["sort_order"]) == ("Вид спереди", 3)
    photo_id = created.json()["id"]
    assert (await api.post("/admin/photos", headers=staff.a, files={"file": jpg}, data={})).status_code == 422

    assert [p["id"] for p in (await api.get(f"/admin/photos?cabinet_id={cabinet.id}", headers=staff.a)).json()["items"]] == [photo_id]
    assert [p["id"] for p in (await api.get(f"/cabinets/{cabinet.id}/photos", headers=staff.o)).json()["items"]] == [photo_id]
    assert (await api.patch(f"/admin/photos/{photo_id}", headers=staff.a, json={"caption": "Сбоку"})).json()["caption"] == "Сбоку"
    assert (await api.delete(f"/admin/photos/{photo_id}", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/photos/{photo_id}", headers=staff.a)).status_code == 404


async def test_document_requests_are_decided_by_operators_too(api, staff, make_user, make_document):
    client = await make_user()
    doc = await make_document(requires_approval=True)
    from app.services.document_service import UserDocumentService
    db = api._transport.app.dependency_overrides  # сессия теста общая для клиента и сервиса
    req = await api.post(f"/documents/{doc.id}/request-access", headers=_auth(client, "user"), json={"user_message": "Нужен"})
    assert req.status_code == 201

    pending = (await api.get("/admin/document-requests?status=pending", headers=staff.o)).json()
    request_id = pending["items"][0]["id"]
    assert (await api.post(f"/admin/document-requests/{request_id}/approve", headers=staff.o, json={})).status_code == 204
    assert (await api.post(f"/admin/document-requests/{request_id}/reject", headers=staff.o, json={"admin_response": "Поздно"})).status_code == 409
    assert (await api.post("/admin/document-requests/999999/approve", headers=staff.o, json={})).status_code == 404


# --- база знаний и FAQ ---

async def test_knowledge_base_administration_over_http(api, staff):
    cat = (await api.post("/admin/kb/categories", headers=staff.a, json={"name": "Насосы"})).json()
    child = (await api.post("/admin/kb/categories", headers=staff.a, json={"name": "Подбор", "parent_id": cat["id"]})).json()
    assert (await api.delete(f"/admin/kb/categories/{cat['id']}", headers=staff.a)).status_code == 409   # есть вложенные
    assert (await api.patch(f"/admin/kb/categories/{cat['id']}", headers=staff.a, json={"name": "Насосное"})).json()["name"] == "Насосное"
    assert [c["id"] for c in (await api.get(f"/admin/kb/categories?parent_id={cat['id']}", headers=staff.o)).json()] == [child["id"]]

    article = await api.post("/admin/kb/articles", headers=staff.a,
                             json={"category_id": cat["id"], "title": "Запуск насоса", "description": "Шаг 1"})
    assert article.status_code == 201
    article_id = article.json()["id"]
    assert (await api.patch(f"/admin/kb/articles/{article_id}", headers=staff.a, json={"is_published": False})).json()["is_published"] is False
    drafts = (await api.get("/admin/kb/articles?is_published=false", headers=staff.o)).json()
    assert [a["id"] for a in drafts["items"]] == [article_id]

    att = await api.post(f"/admin/kb/articles/{article_id}/attachments", headers=staff.a,
                         files={"file": ("схема.pdf", io.BytesIO(b"data"), "application/pdf")})
    assert att.status_code == 201
    assert (await api.delete(f"/admin/kb/articles/{article_id}/attachments/{att.json()['id']}", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/kb/articles/{article_id}", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/kb/categories/{child['id']}", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/kb/categories/{cat['id']}", headers=staff.a)).status_code == 204


async def test_faq_administration_over_http(api, staff):
    cat = (await api.post("/admin/faq/categories", headers=staff.a, json={"name": "Гарантия"})).json()
    child = (await api.post("/admin/faq/categories", headers=staff.a, json={"name": "Сроки", "parent_id": cat["id"]})).json()
    assert (await api.delete(f"/admin/faq/categories/{cat['id']}", headers=staff.a)).status_code == 409
    assert (await api.patch(f"/admin/faq/categories/{cat['id']}", headers=staff.a, json={"name": "Гарантия и сервис"})).json()["name"] == "Гарантия и сервис"
    assert [c["id"] for c in (await api.get("/admin/faq/categories?search=Сроки", headers=staff.o)).json()] == [child["id"]]

    entry = await api.post("/admin/faq/entries", headers=staff.a,
                           json={"category_id": cat["id"], "question": "Как продлить гарантию?", "answer": "Через сервис"})
    assert entry.status_code == 201 and entry.json()["is_published"] is True   # публикуется сразу
    entry_id = entry.json()["id"]
    draft = await api.patch(f"/admin/faq/entries/{entry_id}", headers=staff.a, json={"is_published": False})
    assert (draft.json()["is_published"], draft.json()["version"]) == (False, 2)
    assert [e["id"] for e in (await api.get("/admin/faq/entries?is_published=false", headers=staff.o)).json()["items"]] == [entry_id]
    assert (await api.get("/admin/faq/entries?is_published=true", headers=staff.o)).json()["items"] == []
    assert (await api.post("/admin/faq/entries", headers=staff.a, json={"category_id": cat["id"], "question": "?", "answer": "x"})).status_code == 422
    assert (await api.delete(f"/admin/faq/entries/{entry_id}", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/faq/categories/{child['id']}", headers=staff.a)).status_code == 204


async def test_tags_over_http(api, staff):
    created = await api.post("/admin/tags", headers=staff.a, json={"name": "Важное", "scope": "document"})
    assert created.status_code == 201
    assert (await api.post("/admin/tags", headers=staff.a, json={"name": "важное", "scope": "document"})).status_code == 409
    assert "Важное" in [t["name"] for t in (await api.get("/tags?scope=document", headers=staff.o)).json()]
    assert (await api.delete(f"/admin/tags/{created.json()['id']}", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/tags/{created.json()['id']}", headers=staff.a)).status_code == 404


# --- чаты оператора ---

@pytest.fixture
async def client_chat(db_session, make_user):
    client = await make_user(full_name="Клиент Клиентов")
    chat = await ChatService(db_session).ensure_support_and_notes(client.id)
    await db_session.flush()
    return SimpleNamespace(user=client, chat=chat)


async def test_operator_works_a_chat_over_http(api, staff, client_chat):
    chat_id = client_chat.chat.id
    await api.post(f"/chats/{chat_id}/messages", headers=_auth(client_chat.user, "user"), json={"text": "Помогите"})

    chats = (await api.get("/operator/chats", headers=staff.o)).json()
    mine = next(c for c in chats if c["id"] == chat_id)
    assert mine["user_name"] == "Клиент Клиентов" and mine["unread_count"] == 1
    assert (await api.get("/operator/chats/unread-count", headers=staff.o)).json()
    assert (await api.get(f"/operator/chats/{chat_id}", headers=staff.o)).json()["id"] == chat_id
    assert [m["text"] for m in (await api.get(f"/operator/chats/{chat_id}/messages", headers=staff.o)).json()] == ["Помогите"]

    assert (await api.post(f"/operator/chats/{chat_id}/take", headers=staff.o)).status_code == 204
    reply = await api.post(f"/operator/chats/{chat_id}/messages", headers=staff.o, json={"text": "Уже едем"})
    assert reply.status_code == 201
    msg_id = reply.json()["id"]
    assert [m["id"] for m in (await api.put(f"/operator/chats/{chat_id}/pin/{msg_id}", headers=staff.o)).json()] == [msg_id]
    assert [m["id"] for m in (await api.get(f"/operator/chats/{chat_id}/pinned", headers=staff.o)).json()] == [msg_id]
    assert (await api.delete(f"/operator/chats/{chat_id}/pin/{msg_id}", headers=staff.o)).json() == []
    assert (await api.delete(f"/operator/chats/{chat_id}/pin", headers=staff.o)).json() == []
    assert (await api.put(f"/operator/chats/{chat_id}/pin-chat", headers=staff.o)).status_code == 204
    assert (await api.delete(f"/operator/chats/{chat_id}/pin-chat", headers=staff.o)).status_code == 204
    assert (await api.get(f"/operator/chats/{chat_id}/attachments", headers=staff.o)).json() == []
    assert (await api.post(f"/operator/chats/{chat_id}/return-to-bot", headers=staff.o)).status_code == 204

    found = (await api.get("/operator/messages?q=Помогите", headers=staff.o)).json()
    assert found["total"] >= 1


async def test_operator_chat_settings_and_notes_are_private(api, staff, client_chat):
    notes = next(c for c in [client_chat.chat] if c)  # support-чат; заметки оператору недоступны
    assert (await api.get("/operator/chats/settings", headers=staff.o)).status_code == 200
    assert (await api.patch("/operator/chats/settings", headers=staff.o, json={"font_size": 16})).json()["font_size"] == 16
    assert (await api.get(f"/operator/chats/{notes.id}/settings", headers=staff.o)).status_code == 200
    assert (await api.patch(f"/operator/chats/{notes.id}/settings", headers=staff.o, json={"font_size": 14})).json()["font_size"] == 14
    assert (await api.delete(f"/operator/chats/{notes.id}/settings", headers=staff.o)).status_code == 204
    assert (await api.get("/operator/chats/999999", headers=staff.o)).status_code == 404


async def test_only_admins_wipe_or_delete_chats(api, staff, client_chat):
    chat_id = client_chat.chat.id
    assert (await api.delete(f"/operator/chats/{chat_id}/messages", headers=staff.o)).status_code == 403
    assert (await api.delete(f"/operator/chats/{chat_id}", headers=staff.o)).status_code == 403
    assert (await api.delete(f"/operator/chats/{chat_id}/messages", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/operator/chats/{chat_id}", headers=staff.a)).status_code == 204
    assert (await api.get(f"/operator/chats/{chat_id}", headers=staff.o)).status_code == 404


# --- телеметрия ---

async def test_register_map_over_http(api, staff, make_cabinet):
    cabinet = await make_cabinet(mqtt_topic="t/http")
    created = await api.post("/admin/register-definitions", headers=staff.a,
                             json={"address": 51000, "bit": 3, "name": "Перегрев", "description": "Датчик T1"})
    assert created.status_code == 201
    def_id = created.json()["id"]
    assert (await api.post("/admin/register-definitions", headers=staff.a, json={"address": 51000, "bit": 3, "name": "Дубль"})).status_code == 409
    assert (await api.patch(f"/admin/register-definitions/{def_id}", headers=staff.a, json={"name": "Перегрев двигателя"})).json()["name"] == "Перегрев двигателя"
    assert 51000 in [d["address"] for d in (await api.get("/admin/register-definitions", headers=staff.o)).json()]
    export = await api.get("/admin/register-definitions/export", headers=staff.o)
    assert export.status_code == 200 and "spreadsheetml" in export.headers["content-type"]

    override = await api.post(f"/admin/cabinets/{cabinet.id}/register-overrides", headers=staff.a,
                              json={"address": 51000, "bit": 3, "name": "Перегрев насоса"})
    assert override.status_code == 201
    assert [o["name"] for o in (await api.get(f"/admin/cabinets/{cabinet.id}/register-overrides", headers=staff.o)).json()] == ["Перегрев насоса"]
    assert (await api.get(f"/admin/cabinets/{cabinet.id}/register-map/export", headers=staff.o)).status_code == 200
    assert (await api.patch(f"/admin/cabinets/{cabinet.id}/register-overrides/{override.json()['id']}", headers=staff.a,
                            json={"name": "Новое имя"})).json()["name"] == "Новое имя"
    assert (await api.delete(f"/admin/cabinets/{cabinet.id}/register-overrides/{override.json()['id']}", headers=staff.a)).status_code == 204
    assert (await api.delete(f"/admin/register-definitions/{def_id}", headers=staff.a)).status_code == 204


async def test_cabinet_telemetry_for_staff(api, staff, make_cabinet):
    cabinet = await make_cabinet()

    assert (await api.get(f"/admin/cabinets/{cabinet.id}/telemetry", headers=staff.o)).json() == {"registers": []}
    history = (await api.get(f"/admin/cabinets/{cabinet.id}/telemetry/history", headers=staff.o)).json()
    assert history["total"] == 0
    assert (await api.get("/admin/cabinets/999999/telemetry", headers=staff.o)).status_code == 404


# --- служебные действия ---

async def test_bot_maintenance_over_http(api, staff):
    started = await api.post("/admin/bot/reindex?scope=faq", headers=staff.a)
    assert started.status_code == 202 and started.json()["status"] == "started"
    assert (await api.post("/admin/bot/reindex?scope=nonsense", headers=staff.a)).status_code == 422
    pruned = await api.post("/admin/bot/prune", headers=staff.a)
    assert pruned.status_code == 200 and pruned.json()["status"] == "ok"
    assert (await api.post("/admin/bot/prune", headers=staff.o)).status_code == 403


async def test_broadcast_and_promo_over_http(api, staff, make_user, db_session):
    from sqlalchemy import delete
    from app.models.promo_message import PromoMessage
    await db_session.execute(delete(PromoMessage))  # миграция заводит стартовые заготовки
    client = await make_user()

    sent = await api.post("/admin/notifications/broadcast", headers=staff.a, json={"title": "Акция", "body": "Скидка", "role": "user"})
    assert sent.status_code == 200 and sent.json()["sent_to"] >= 1
    assert (await api.post("/admin/notifications/broadcast", headers=staff.o, json={"title": "Акция", "body": "x"})).status_code == 403

    assert (await api.post("/admin/notifications/promo/send", headers=staff.a)).status_code == 400   # заготовок нет
    msg = await api.post("/admin/notifications/promo/messages", headers=staff.a, json={"title": "Скидка", "body": "10% на сервис"})
    assert msg.status_code == 201
    promo_id = msg.json()["id"]
    promo = await api.post(f"/admin/notifications/promo/send?role=user&promo_id={promo_id}", headers=staff.a)
    assert promo.status_code == 200 and promo.json()["message"]["id"] == promo_id
    assert (await api.post("/admin/notifications/promo/send?promo_id=999999", headers=staff.a)).status_code == 404
    assert (await api.patch(f"/admin/notifications/promo/messages/{promo_id}", headers=staff.a, json={"title": "Новая скидка"})).json()["title"] == "Новая скидка"
    assert (await api.get("/admin/notifications/promo/schedule", headers=staff.a)).status_code == 200
    assert (await api.patch("/admin/notifications/promo/schedule", headers=staff.a, json={"enabled": False})).status_code == 200
    assert [m["id"] for m in (await api.get("/admin/notifications/promo/messages", headers=staff.a)).json()] == [promo_id]
    assert (await api.delete(f"/admin/notifications/promo/messages/{promo_id}", headers=staff.a)).status_code == 204


# --- рекламации ---

async def test_operator_reads_reclamations_but_does_not_manage_bitrix_side(api, staff, make_reclamation):
    reclamation = await make_reclamation(description="Не работает кнопка")

    listed = await api.get("/admin/reclamations", headers=staff.o)
    card = await api.get(f"/admin/reclamations/{reclamation.id}", headers=staff.o)

    assert listed.status_code == 200 and reclamation.id in [r["id"] for r in listed.json()["items"]]
    assert card.status_code == 200 and card.json()["id"] == reclamation.id
    assert (await api.get("/admin/reclamations", headers=staff.a)).status_code == 200
    for method, url in (
        ("GET", "/admin/reclamations/bitrix-outbox"),
        ("GET", "/admin/reclamations/bitrix-detached"),
        ("DELETE", "/admin/reclamations/bitrix-outbox/1"),
        ("DELETE", f"/admin/reclamations/{reclamation.id}"),
    ):
        assert (await api.request(method, url, headers=staff.o)).status_code == 403, url
