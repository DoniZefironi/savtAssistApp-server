"""Оставшиеся ручки и вспомогательные части: фильтры журнала и списка клиентов,
привязка ШУ у пользователя, скачивание файлов, проверки форм, синхронизация папок по
кнопке, фоновая переиндексация из админки, подпись ссылок, инициализация Firebase,
служебные точки приложения. Внешнее подменено."""
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core import dependencies, firebase
from app.core.limiter import limiter
from app.core.security import create_access_token
from app.core.signed_urls import sign_url, sign_url_long, strip_signature, verify_signature
from app.repositories.messenger import MessengerLinkRepository
from app.routers import admin_bot, admin_faq, admin_kb
from app.services import bot_indexer, kb_service, project_folder_service, upload_service


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


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    _silence_background_tasks(monkeypatch)


@pytest.fixture
async def staff(make_user):
    return SimpleNamespace(
        o=_auth(await make_user("operator"), "operator"),
        a=_auth(await make_user("admin"), "admin"),
        s=_auth(await make_user("superadmin"), "superadmin"),
    )


# --- журнал действий: все фильтры и поиск ---

async def test_audit_journal_filters(api, staff, make_user, db_session):
    from app.services.audit_service import AuditLogger
    actor = await make_user("admin", full_name="Админ Анна")
    logger = AuditLogger(db_session)
    logger.log("cabinet.create", "cabinet", 11, actor.id, "admin", {"object_number": "29_777"})
    logger.log("user.ban", "user", 12, actor.id, "admin", {"reason": "спам"})
    logger.log("project.delete", "project", 13, None, "system", {})
    await db_session.flush()

    def ask(**params):
        return api.get("/admin/audit-logs", params={"size": 200, **params}, headers=staff.s)

    assert {i["action"] for i in (await ask(action="cabinet.create")).json()["items"]} == {"cabinet.create"}
    assert {i["entity_type"] for i in (await ask(entity_type="user", actor_id=actor.id)).json()["items"]} == {"user"}
    assert [i["entity_id"] for i in (await ask(entity_id=13)).json()["items"]] == [13]
    assert {i["actor_role"] for i in (await ask(actor_role="system")).json()["items"]} == {"system"}
    now = datetime.now(timezone.utc)
    assert (await ask(date_from=(now - timedelta(minutes=5)).isoformat())).json()["total"] >= 3
    assert (await ask(date_to=(now - timedelta(days=1)).isoformat())).json()["total"] == 0
    for where in ("all", "action", "entity_type", "actor_name", "payload"):
        query = {"all": "29_777", "action": "cabinet", "entity_type": "project", "actor_name": "Анна", "payload": "спам"}[where]
        found = (await ask(search=query, search_in=where)).json()
        assert found["total"] >= 1, where
    for sort_by in ("created_at", "action", "entity_type", "actor_role", "actor_id"):
        for order in ("asc", "desc"):
            assert (await ask(sort_by=sort_by, sort_order=order)).status_code == 200
    assert (await ask(search="x", search_in="nonsense")).status_code == 422


# --- список клиентов: фильтры и сортировка ---

async def test_client_list_filters_and_sorting(api, staff, make_user):
    verified = await make_user(full_name="Верифицированный", is_verified=True, user_type="organization", organization_name="ООО Ромашка")
    unverified = await make_user(full_name="Неподтверждённый", is_verified=False, is_phone_verified=False, is_active=False)

    def ask(**params):
        return api.get("/admin/users", params={"size": 100, **params}, headers=staff.o)

    def ids(response):
        return {u["id"] for u in response.json()["items"]}

    assert verified.id in ids(await ask(is_verified=True)) and unverified.id not in ids(await ask(is_verified=True))
    assert unverified.id in ids(await ask(is_active=False, is_phone_verified=False))
    assert verified.id in ids(await ask(user_type="organization", search="Ромашка"))
    for sort_by in ("created_at", "full_name", "phone", "email", "login", "organization_name", "role"):
        for order in ("asc", "desc"):
            assert (await ask(sort_by=sort_by, sort_order=order)).status_code == 200


async def test_extra_staff_and_cabinet_user_routes(api, staff, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet):
    created_admin = await api.post("/admin/users/admins", headers=staff.s, json={"login": "boss-2", "password": "password8"})
    created_operator = await api.post("/admin/operators", headers=staff.a, json={"login": "oper-2", "password": "password8"})
    assert created_admin.status_code == 201 and created_operator.status_code == 201
    assert (await api.delete(f"/admin/users/operators/{created_operator.json()['id']}", headers=staff.a)).status_code == 204

    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    via_project, direct, stranger = await make_user(), await make_user(), await make_user()
    await link_user_project(via_project, project)
    await link_user_cabinet(direct, cabinet)
    remove = lambda user: api.request(   # noqa: E731
        "DELETE", f"/admin/cabinets/{cabinet.id}/users/{user.id}", headers=staff.a, json={"reason": "Больше не нужен"},
    )
    assert (await remove(via_project)).status_code == 409        # доступ через проект — убирать из проекта
    assert (await remove(stranger)).status_code == 404
    assert (await remove(direct)).status_code == 204


# --- документы и фото: формы и файлы ---

async def test_document_and_photo_forms_validate_ids(api, staff):
    pdf = {"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")}
    jpg = {"file": ("a.jpg", io.BytesIO(b"x"), "image/jpeg")}

    for url, files in (("/admin/documents", pdf), ("/admin/photos", jpg)):
        assert (await api.post(url, headers=staff.a, files=files, data={"cabinet_id": "abc"})).status_code == 422
        assert (await api.post(url, headers=staff.a, files=files, data={"project_id": "1x"})).status_code == 422


async def test_clients_download_free_documents_and_staff_list_project_photos(
    api, staff, make_user, make_project, make_cabinet, link_user_project, make_document, tmp_path, monkeypatch,
):
    uploads = tmp_path / "uploads"
    (uploads / "documents").mkdir(parents=True)
    (uploads / "documents" / "d.pdf").write_bytes(b"%PDF-doc")
    monkeypatch.setattr(upload_service, "UPLOAD_ROOT", uploads)
    from app.routers import documents as documents_router
    monkeypatch.setattr("app.services.document_service.UPLOAD_ROOT", uploads)
    client = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(client, project)
    doc = await make_document(cabinet_id=cabinet.id, title="Руководство", file_url="/static/documents/d.pdf", requires_approval=False)

    downloaded = await api.get(f"/documents/{doc.id}/download", headers=_auth(client, "user"))
    listed = await api.get(f"/cabinets/{cabinet.id}/documents", headers=_auth(client, "user"))
    photos = await api.get(f"/projects/{project.id}/photos", headers=staff.o)

    assert downloaded.status_code == 200 and downloaded.content == b"%PDF-doc"
    assert listed.json()["items"][0]["id"] == doc.id and photos.json()["total"] == 0 and documents_router


async def test_kb_attachment_download(api, make_user, db_session, tmp_path, monkeypatch):
    from app.schemas.kb import KbArticleCreateIn, KbCategoryCreateIn
    from app.services.kb_service import KbArticleService, KbCategoryService
    uploads = tmp_path / "uploads"
    (uploads / "documents").mkdir(parents=True)
    (uploads / "documents" / "schema.pdf").write_bytes(b"%PDF-kb")
    monkeypatch.setattr(kb_service, "UPLOAD_ROOT", uploads)
    category = await KbCategoryService(db_session).create(KbCategoryCreateIn(name="Общее"))
    service = KbArticleService(db_session)
    article = await service.create(KbArticleCreateIn(category_id=category.id, title="Со схемой"))
    other = await service.create(KbArticleCreateIn(category_id=category.id, title="Другая"))
    from app.models.kb_article_attachment import KbArticleAttachment
    att = KbArticleAttachment(article_id=article.id, file_url="/static/documents/schema.pdf", file_size_bytes=7,
                              doc_type="pdf", mime_type="application/pdf", title="Схема.pdf")
    db_session.add(att)
    await db_session.flush()
    headers = _auth(await make_user(), "user")

    ok = await api.get(f"/kb/articles/{article.id}/attachments/{att.id}/download", headers=headers)
    wrong = await api.get(f"/kb/articles/{other.id}/attachments/{att.id}/download", headers=headers)

    assert ok.status_code == 200 and ok.content == b"%PDF-kb" and wrong.status_code == 404


# --- чаты и теги: мелкие ручки ---

async def test_global_chat_settings_and_message_edit_rules(api, make_user, db_session):
    from app.services.chat_service import ChatService
    user = await make_user()
    headers = _auth(user, "user")
    chat = await ChatService(db_session).ensure_support_and_notes(user.id)
    await db_session.flush()
    sent = await api.post(f"/chats/{chat.id}/messages", headers=headers, json={"text": "Привет"})
    msg_id = sent.json()["id"]

    assert (await api.get("/chats/settings", headers=headers)).status_code == 200
    assert (await api.patch(f"/chats/{chat.id}/messages/{msg_id}", headers=headers,
                            json={"attachments": [{"file_url": "/static/files/a.pdf", "file_name": "a.pdf",
                                                   "file_size_bytes": 1, "mime_type": "application/pdf"}]})).status_code == 422


async def test_article_tags_route(api, staff, db_session):
    from app.schemas.kb import KbArticleCreateIn, KbCategoryCreateIn
    from app.services.kb_service import KbArticleService, KbCategoryService
    category = await KbCategoryService(db_session).create(KbCategoryCreateIn(name="Общее"))
    article = await KbArticleService(db_session).create(KbArticleCreateIn(category_id=category.id, title="С тегами"))

    assert (await api.put(f"/admin/kb-articles/{article.id}/tags", headers=staff.a, json={"tag_ids": []})).status_code == 204


# --- синхронизация папок по кнопке ---

async def test_folder_sync_buttons(api, staff, make_project, tmp_path, monkeypatch):
    project = await make_project(name="26_100 Космос", production_number="26_100")
    monkeypatch.setattr(settings, "project_folders_root", "")
    assert (await api.post(f"/admin/projects/{project.id}/sync-folder", headers=staff.o)).status_code == 404
    assert (await api.post("/admin/projects/sync-folders", headers=staff.a)).status_code == 404

    monkeypatch.setattr(settings, "project_folders_root", str(tmp_path))

    async def sync_one(session, proj):
        proj.folder_synced_at = datetime.now(timezone.utc)

    async def sync_all(session):
        return {"total": 4, "synced": 3, "relocated": 1, "failed": 0}

    monkeypatch.setattr(project_folder_service, "sync_project_folder", sync_one)
    monkeypatch.setattr(project_folder_service, "_sync_all_projects", sync_all)

    one = await api.post(f"/admin/projects/{project.id}/sync-folder", headers=staff.o)
    assert one.status_code == 200 and one.json()["imported_documents"] == 0
    assert (await api.post("/admin/projects/sync-folders", headers=staff.o)).status_code == 403    # все проекты — только админ
    everything = await api.post("/admin/projects/sync-folders", headers=staff.a)
    assert everything.status_code == 200 and everything.json()["total_projects"] == 4


# --- фоновая переиндексация из админки ---

async def test_admin_reindex_tasks(db_session, monkeypatch):
    done = []
    tasks = []

    async def index_kb(session, article):
        done.append(("kb", article.id))

    async def index_faq(session, entry):
        done.append(("faq", entry.id))

    async def reindex_all(session, force=False, scope="all", project_id=None):
        done.append(("all", force, scope, project_id))
        return {"ok": 1}

    from app.schemas.faq import FaqCategoryCreateIn, FaqEntryCreateIn
    from app.schemas.kb import KbArticleCreateIn, KbCategoryCreateIn
    from app.services.faq_service import FaqCategoryService, FaqEntryService
    from app.services.kb_service import KbArticleService, KbCategoryService
    kb_cat = await KbCategoryService(db_session).create(KbCategoryCreateIn(name="Общее"))
    article = await KbArticleService(db_session).create(KbArticleCreateIn(category_id=kb_cat.id, title="Статья"))
    faq_cat = await FaqCategoryService(db_session).create(FaqCategoryCreateIn(name="Общее"))
    entry = await FaqEntryService(db_session).create(FaqEntryCreateIn(category_id=faq_cat.id, question="Вопрос тут?", answer="Ответ"))
    for module in (admin_kb, admin_faq, admin_bot):
        monkeypatch.setattr(module, "spawn", tasks.append)
        monkeypatch.setattr(module, "AsyncSessionLocal", lambda: _SessionContext(db_session))
    monkeypatch.setattr(bot_indexer, "index_kb_article", index_kb)
    monkeypatch.setattr(bot_indexer, "index_faq_entry", index_faq)
    monkeypatch.setattr(admin_bot, "reindex_all", reindex_all)

    admin_kb._reindex_kb(article.id)
    admin_kb._reindex_kb(999999)                  # статьи уже нет — тихо ничего не делаем
    admin_faq._reindex_faq(entry.id)
    admin_faq._reindex_faq(999999)
    for task in tasks:
        await task
    assert done == [("kb", article.id), ("faq", entry.id)]

    tasks.clear()
    await admin_bot.reindex(force=True, scope="faq", project_id=None, _=None)
    await tasks[0]
    assert done[-1] == ("all", True, "faq", None)


# --- подпись ссылок ---

def test_signed_links(monkeypatch):
    monkeypatch.setattr(settings, "static_link_secret", "")
    assert sign_url("/static/a.jpg") == "/static/a.jpg" and verify_signature("/static/a.jpg") is True   # локально без секрета

    monkeypatch.setattr(settings, "static_link_secret", "secret")
    signed = sign_url("/static/a.jpg")
    assert signed.startswith("/static/a.jpg?md5=") and verify_signature(signed) is True
    assert sign_url("https://external.example/a.jpg") == "https://external.example/a.jpg" and sign_url(None) is None
    assert strip_signature(signed) == "/static/a.jpg" and strip_signature(None) is None and strip_signature("") == ""
    assert verify_signature(None) is False and verify_signature("/static/a.jpg") is False
    assert verify_signature("/static/a.jpg?md5=abc&expires=notanumber") is False
    assert verify_signature(signed.replace("/a.jpg", "/b.jpg")) is False                               # подпись к другому файлу
    assert verify_signature(sign_url("/static/a.jpg", ttl_seconds=-10)) is False                       # просрочена
    assert verify_signature("/static/a.jpg?expires=9999999999") is False                              # нет подписи
    assert "expires=" in sign_url_long("/static/a.jpg")


# --- токены в заголовке и служебные точки ---

async def test_role_from_token_never_raises():
    from fastapi.security import HTTPAuthorizationCredentials

    make = lambda token: HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)    # noqa: E731
    good = create_access_token(user_id=1, role="operator")

    assert await dependencies.get_role_from_token(make(good)) == "operator"
    assert await dependencies.get_role_from_token(None) == "unknown"
    assert await dependencies.get_role_from_token(make("мусор")) == "unknown"


async def test_health_and_root(api, monkeypatch):
    """Проверка живости ходит в БД через общий пул приложения; в тесте он подменён,
    чтобы соединение не пережило цикл событий теста."""
    from app import main

    class _Connection:
        async def execute(self, statement):
            return SimpleNamespace(scalar=lambda: 1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(main, "engine", SimpleNamespace(connect=lambda: _Connection()))

    health = await api.get("/health")
    assert health.status_code == 200 and health.json() == {"app": "ok", "db": True}
    assert (await api.get("/")).json() == {"service": "savt-assist", "status": "ok"}


async def test_failed_request_rolls_the_session_back(api, make_user):
    user = await make_user()
    # ошибка внутри обработчика проходит через get_session и не оставляет сессию в подвешенном состоянии
    assert (await api.get("/chats/999999/messages", headers=_auth(user, "user"))).status_code == 404
    assert (await api.get("/auth/me", headers=_auth(user, "user"))).status_code == 200


# --- Firebase ---

def test_firebase_initialization(monkeypatch, tmp_path):
    import firebase_admin
    from firebase_admin import credentials

    monkeypatch.setattr(firebase, "_initialized", False)
    firebase.init_firebase("")
    firebase.init_firebase(str(tmp_path / "missing.json"))
    assert firebase.is_firebase_ready() is False

    key = tmp_path / "key.json"
    key.write_text("{}")
    started = []
    monkeypatch.setattr(credentials, "Certificate", lambda path: ("cert", path))
    monkeypatch.setattr(firebase_admin, "initialize_app", lambda cred: started.append(cred))
    firebase.init_firebase(str(key))
    firebase.init_firebase(str(key))                         # повторно — ничего не делаем
    assert firebase.is_firebase_ready() is True and started == [("cert", str(key))]

    monkeypatch.setattr(firebase, "_initialized", False)

    def broken(cred):
        raise ValueError("битые ключи")

    monkeypatch.setattr(firebase_admin, "initialize_app", broken)
    firebase.init_firebase(str(key))
    assert firebase.is_firebase_ready() is False


# --- привязки мессенджера ---

async def test_messenger_links_upsert_and_delete(db_session, make_user):
    user = await make_user()
    repo = MessengerLinkRepository(db_session)

    first = await repo.upsert(user.id, "telegram", "100")
    again = await repo.upsert(user.id, "telegram", "200")
    assert first.id == again.id and again.external_chat_id == "200"
    assert (await repo.find_by_chat("telegram", "200")).user_id == user.id

    await repo.delete_by_user_and_channel(user.id, "telegram")
    await repo.delete_by_user_and_channel(user.id, "telegram")       # повторно — не ошибка
    assert await repo.find_by_chat("telegram", "200") is None
