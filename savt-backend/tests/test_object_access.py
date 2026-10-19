"""Доступ к чужим данным. Владелец заводит проект, ШУ, три чата, сообщение, документы,
уведомление, избранное, токен устройства и рекламацию. Посторонний пользователь (ни к чему
не привязан) обходит с идентификаторами этих объектов каждую ручку, доступную любому
вошедшему: чужое не должно ни читаться, ни меняться, а в ответах нигде не должно быть
данных владельца. Список ручек берётся из самого приложения (как в матрице прав), поэтому
новая ручка с идентификатором в адресе попадает под проверку сама. Если тест шлёт неверное
тело запроса (422), проверка была бы пустой, поэтому такой ответ тоже считается ошибкой."""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.limiter import limiter
from app.core.security import create_access_token
from app.main import app
from app.models.cabinets import Cabinet
from app.models.chat import Chat
from app.models.chat_pinned_message import ChatPinnedMessage
from app.models.device_token import DeviceToken
from app.models.document import Document
from app.models.message import Message
from app.models.message_reaction import MessageReaction
from app.models.notification import Notification
from app.models.project import Project
from app.models.user_favorite import UserFavorite
from tests.route_permissions import compute

SECRET = "СЕКРЕТ-ВЛАДЕЛЬЦА"
OWNER_FCM_TOKEN = "owner-fcm-token-secret"

_PERMISSIONS = compute(app)
USER_ROUTES = sorted((m, p) for (m, p), level in _PERMISSIONS.items() if level == "authenticated" and "{" in p)
LIST_ROUTES = sorted(p for (m, p), level in _PERMISSIONS.items() if m == "GET" and level == "authenticated" and "{" not in p)

# Идемпотентное удаление СВОЕГО: для постороннего «успех» значит «нечего удалять», чужое
# при этом не трогается — это проверяет сверка состояния после запросов
HARMLESS_SUCCESS = {
    ("DELETE", "/favorites/{entity_type}/{entity_id}"),
    ("DELETE", "/device-tokens/{token}"),
}


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)


def _body(method: str, path: str, world):
    bodies = {
        ("PATCH", "/cabinets/{cabinet_id}"): {"custom_name": "взлом"},
        ("POST", "/chats/{chat_id}/messages"): {"text": "взлом"},
        ("PATCH", "/chats/{chat_id}/messages/{msg_id}"): {"text": "взлом"},
        ("DELETE", "/chats/{chat_id}/messages"): {"message_ids": [world.message_id]},
        ("PATCH", "/chats/{chat_id}/wallpaper"): {"wallpaper_url": None},
        ("PATCH", "/chats/{chat_id}/settings"): {"font_size": 12},
        ("POST", "/documents/{doc_id}/request-access"): {"user_message": "хочу"},
    }
    return bodies.get((method, path))


@pytest.fixture
async def world(db_session, make_user, make_project, make_cabinet, link_user_project, make_chat, make_document,
                make_reclamation):
    owner = await make_user(full_name=f"{SECRET} владелец")
    outsider = await make_user()

    project = await make_project(name=f"Проект {SECRET}")
    cabinet = await make_cabinet(project_id=project.id, unique_code="OWNERCAB0000000000000000000001")
    await link_user_project(owner, project)

    cabinet_chat = await make_chat(owner, "cabinet", cabinet_id=cabinet.id)
    project_chat = await make_chat(owner, "project", project_id=project.id)
    support_chat = await make_chat(owner, "support")
    outsider_chat = await make_chat(outsider, "support")

    message = Message(chat_id=cabinet_chat.id, sender_id=owner.id, text=f"{SECRET} сообщение")
    notification = Notification(user_id=owner.id, type="chat_message", title=SECRET, body=SECRET)
    device = DeviceToken(user_id=owner.id, token=OWNER_FCM_TOKEN, platform="android")
    db_session.add_all([message, notification, device])
    await db_session.flush()

    open_doc = await make_document(cabinet_id=cabinet.id, title=f"{SECRET} открытый", requires_approval=False)
    locked_doc = await make_document(cabinet_id=cabinet.id, title=f"{SECRET} закрытый", requires_approval=True)
    favorite = UserFavorite(user_id=owner.id, entity_type="document", entity_id=open_doc.id)
    db_session.add(favorite)
    await db_session.flush()
    reclamation = await make_reclamation(user=owner, project_id=project.id, description=SECRET)

    # Только числа и строки: после первого запроса объекты сессии «устаревают», и обращение
    # к их полям вне асинхронного контекста падает
    return SimpleNamespace(
        owner_id=owner.id, outsider_chat_id=outsider_chat.id,
        owner_phone=owner.phone, project_id=project.id, cabinet_id=cabinet.id,
        cabinet_code=cabinet.unique_code, message_id=message.id, notification_id=notification.id,
        favorite_id=favorite.id, device_id=device.id, open_doc_id=open_doc.id, locked_doc_id=locked_doc.id,
        reclamation_id=reclamation.id, chat_ids=[cabinet_chat.id, project_chat.id, support_chat.id],
        headers={"Authorization": f"Bearer {create_access_token(user_id=outsider.id, role='user')}"},
    )


def _param_sets(path: str, world):
    base = {
        "cabinet_id": world.cabinet_id, "project_id": world.project_id, "msg_id": world.message_id,
        "notif_id": world.notification_id, "reclamation_id": world.reclamation_id,
        "entity_type": "document", "entity_id": world.open_doc_id, "emoji": "👍",
        "token": OWNER_FCM_TOKEN, "unique_code": world.cabinet_code,
    }
    doc_ids = [world.open_doc_id, world.locked_doc_id] if "{doc_id}" in path else [None]
    chat_ids = world.chat_ids if "{chat_id}" in path else [None]
    for chat_id in chat_ids:
        for doc_id in doc_ids:
            yield {**base, "chat_id": chat_id, "doc_id": doc_id}


async def _assert_owner_data_untouched(db_session, world):
    db_session.expire_all()
    problems = []

    for chat_id in world.chat_ids:
        row = await db_session.get(Chat, chat_id)
        if row is None or row.archived_at is not None:
            problems.append(f"чат {chat_id} удалён или заархивирован")

    message = await db_session.get(Message, world.message_id)
    if message is None or message.deleted_at is not None or message.edited_at is not None \
            or message.text != f"{SECRET} сообщение":
        problems.append("сообщение владельца удалено или изменено")

    notification = await db_session.get(Notification, world.notification_id)
    if notification is None or notification.is_read:
        problems.append("уведомление владельца удалено или прочитано")

    if await db_session.get(UserFavorite, world.favorite_id) is None:
        problems.append("избранное владельца удалено")
    if await db_session.get(DeviceToken, world.device_id) is None:
        problems.append("токен устройства владельца удалён")

    cabinet = await db_session.get(Cabinet, world.cabinet_id)
    if cabinet is None or cabinet.deleted_at is not None:
        problems.append("ШУ владельца удалён")
    if await db_session.get(Project, world.project_id) is None:
        problems.append("проект владельца удалён")
    for doc_id in (world.open_doc_id, world.locked_doc_id):
        if await db_session.get(Document, doc_id) is None:
            problems.append(f"документ {doc_id} удалён")

    for model, label in ((ChatPinnedMessage, "закрепы сообщений"), (MessageReaction, "реакции")):
        count = (await db_session.execute(select(func.count()).select_from(model))).scalar_one()
        if count:
            problems.append(f"посторонний оставил {label}: {count}")

    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("method,path", USER_ROUTES, ids=lambda value: str(value))
async def test_outsider_cannot_reach_foreign_objects(api, db_session, world, method, path):
    problems = []
    for params in _param_sets(path, world):
        url = path.format(**params)
        response = await api.request(method, url, headers=world.headers, json=_body(method, path, world))

        if response.status_code == 422:
            problems.append(f"{method} {url}: 422 — тест послал неверное тело запроса, проверка пустая")
        elif (method, path) not in HARMLESS_SUCCESS and not 400 <= response.status_code < 500:
            problems.append(f"{method} {url}: ответ {response.status_code} вместо отказа")
        if SECRET in response.text or OWNER_FCM_TOKEN in response.text:
            problems.append(f"{method} {url}: в ответе данные владельца")

    assert not problems, "\n".join(problems)
    await _assert_owner_data_untouched(db_session, world)


@pytest.mark.parametrize("path", LIST_ROUTES)
async def test_lists_of_an_outsider_contain_nothing_of_the_owner(api, world, path):
    response = await api.get(path, headers=world.headers)

    assert SECRET not in response.text and OWNER_FCM_TOKEN not in response.text
    assert world.owner_phone not in response.text


@pytest.mark.parametrize("path", LIST_ROUTES)
async def test_lists_ignore_foreign_ids_and_search_for_foreign_text(api, world, path):
    # те же списки, но с идентификаторами и поисковой строкой, нацеленными на чужое
    params = {
        "search": SECRET, "q": SECRET, "query": SECRET, "cabinet_id": world.cabinet_id,
        "project_id": world.project_id, "chat_id": world.chat_ids[0], "user_id": world.owner_id,
    }
    response = await api.get(path, params=params, headers=world.headers)

    assert response.status_code != 500
    assert SECRET not in response.text and world.owner_phone not in response.text


# --- идентификаторы чужих объектов в теле запроса ---

_REQUEST_TEXT = "Не включается шкаф, нужен ремонт"


async def test_outsider_cannot_open_a_service_request_on_foreign_objects(api, db_session, world):
    from app.models.service_request import ServiceRequest

    for target in ({"cabinet_id": world.cabinet_id}, {"project_id": world.project_id}):
        response = await api.post(
            "/service-requests", headers=world.headers,
            json={**target, "request_type": "repair", "description": _REQUEST_TEXT},
        )
        assert 400 <= response.status_code < 500 and response.status_code != 422, (target, response.text)

    count = (await db_session.execute(select(func.count()).select_from(ServiceRequest))).scalar_one()
    assert count == 0


async def test_outsider_cannot_ask_to_add_a_cabinet_to_a_foreign_project(api, db_session, world):
    from app.models.cabinet_addition_request import CabinetAdditionRequest

    response = await api.post(
        "/cabinets/add-by-photo", headers=world.headers,
        json={"project_id": world.project_id, "photo_url": "/static/photos/x.jpg", "user_comment": "нет шкафа"},
    )

    assert 400 <= response.status_code < 500 and response.status_code != 422, response.text
    count = (await db_session.execute(select(func.count()).select_from(CabinetAdditionRequest))).scalar_one()
    assert count == 0


async def test_outsider_cannot_favorite_a_document_he_cannot_see(api, db_session, world):
    # без этого по ответу можно перебирать номера документов и узнавать, какие существуют
    for doc_id in (world.open_doc_id, world.locked_doc_id):
        response = await api.post(
            "/favorites", headers=world.headers, json={"entity_type": "document", "entity_id": doc_id},
        )
        assert response.status_code == 404, (doc_id, response.status_code, response.text)


async def test_outsider_cannot_reply_to_a_message_of_a_foreign_chat(api, db_session, world):
    response = await api.post(
        f"/chats/{world.outsider_chat_id}/messages", headers=world.headers,
        json={"text": "привет", "reply_to_message_id": world.message_id},
    )

    assert 400 <= response.status_code < 500 and response.status_code != 422, (response.status_code, response.text)
    assert SECRET not in response.text


@pytest.mark.parametrize("method,path", USER_ROUTES, ids=lambda value: str(value))
async def test_anonymous_cannot_reach_user_routes(api, world, method, path):
    params = next(_param_sets(path, world))
    response = await api.request(method, path.format(**params), json=_body(method, path, world))

    assert response.status_code in (401, 403), f"{method} {path}: ответ {response.status_code}"
