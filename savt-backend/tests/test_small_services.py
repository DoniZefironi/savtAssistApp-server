"""Небольшие сервисы: избранное, теги, QR-коды, доставка в Telegram и push.
Сеть не используется: Telegram и FCM подменяются и записываются."""
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.device_token import DeviceToken
from app.models.document_tag import DocumentTag
from app.models.kb_article_tag import KbArticleTag
from app.models.kbarticle import KbArticle
from app.models.kbcategory import KbCategory
from app.repositories.notification import NotificationRepository
from app.schemas.favorites import FavoriteIn
from app.schemas.tags import TagCreateIn
from app.services import messenger_service, push_service, qr_service
from app.services.favorite_service import FavoriteService
from app.services.tag_service import TagService


# --- избранное ---

async def test_favorites_add_list_remove(db_session, make_user):
    user, other = await make_user(), await make_user()
    svc = FavoriteService(db_session)

    doc = await svc.add(user.id, FavoriteIn(entity_type="document", entity_id=5))
    await svc.add(user.id, FavoriteIn(entity_type="faq_entry", entity_id=5))  # тот же id, другой тип
    await svc.add(other.id, FavoriteIn(entity_type="document", entity_id=5))  # у другого человека своё

    assert (doc.entity_type, doc.entity_id) == ("document", 5)
    assert (await svc.list_favorites(user.id)).total == 2
    only_docs = await svc.list_favorites(user.id, entity_type="document")
    assert [f.entity_type for f in only_docs.items] == ["document"]
    with pytest.raises(AlreadyExistsError):
        await svc.add(user.id, FavoriteIn(entity_type="document", entity_id=5))

    await svc.remove(user.id, "document", 5)
    assert (await svc.list_favorites(user.id)).total == 1
    assert (await svc.list_favorites(other.id)).total == 1
    with pytest.raises(NotFoundError):
        await svc.remove(user.id, "document", 5)


async def test_favorites_are_paginated(db_session, make_user):
    user = await make_user()
    svc = FavoriteService(db_session)
    for n in range(1, 6):
        await svc.add(user.id, FavoriteIn(entity_type="document", entity_id=n))

    page = await svc.list_favorites(user.id, page=2, size=2)

    assert page.total == 5 and len(page.items) == 2


@pytest.mark.parametrize("kwargs", [
    {"entity_type": "cabinet", "entity_id": 1},
    {"entity_type": "document", "entity_id": 0},
])
def test_favorite_schema_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        FavoriteIn(**kwargs)


# --- теги ---

async def test_tags_create_list_delete(db_session):
    svc = TagService(db_session)

    manual = await svc.create(TagCreateIn(name="Инструкция", scope="document"))
    await svc.create(TagCreateIn(name="Насосная", scope="cabinet"))
    await svc.create(TagCreateIn(name="Инструкция", scope="cabinet"))  # то же имя в другой области можно

    assert {t.name for t in await svc.list_all(scope="cabinet")} == {"Насосная", "Инструкция"}
    assert [t.id for t in await svc.list_all(scope="document")] == [manual.id]
    with pytest.raises(AlreadyExistsError):
        await svc.create(TagCreateIn(name="инструкция", scope="document"))  # регистр не важен

    await svc.delete(manual.id)
    assert await svc.list_all(scope="document") == []
    with pytest.raises(NotFoundError):
        await svc.delete(manual.id)


async def test_set_document_tags_replaces_the_set(db_session, make_document):
    svc = TagService(db_session)
    first, second = await svc.create(TagCreateIn(name="Один")), await svc.create(TagCreateIn(name="Два"))
    doc = await make_document()

    await svc.set_document_tags(doc.id, [first.id, second.id])
    await svc.set_document_tags(doc.id, [second.id])

    rows = (await db_session.execute(select(DocumentTag.tag_id).where(DocumentTag.document_id == doc.id))).scalars().all()
    assert rows == [second.id]
    await svc.set_document_tags(doc.id, [])
    assert (await db_session.execute(select(DocumentTag).where(DocumentTag.document_id == doc.id))).first() is None


async def test_set_article_tags_replaces_the_set(db_session):
    svc = TagService(db_session)
    tag = await svc.create(TagCreateIn(name="Статейный"))
    category = KbCategory(name="Общее", slug="obschee-1")
    db_session.add(category)
    await db_session.flush()
    article = KbArticle(category_id=category.id, title="Статья", slug="statya-1")
    db_session.add(article)
    await db_session.flush()

    await svc.set_article_tags(article.id, [tag.id])
    await svc.set_article_tags(article.id, [tag.id])  # повтор не плодит дубли

    assert len((await db_session.execute(select(KbArticleTag).where(KbArticleTag.article_id == article.id))).scalars().all()) == 1


# --- QR-коды ---

def test_qr_is_a_square_png():
    png = qr_service.generate_qr("https://helper.savt.by/add/project?code=abc")

    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.width == image.height and image.width > 200


def test_qr_differs_by_content_and_logo_is_overlaid(monkeypatch):
    a, b = qr_service.generate_qr("one"), qr_service.generate_qr("two")
    assert a != b

    monkeypatch.setattr(qr_service, "LOGO_PATH", qr_service.Path("/no/such/logo.png"))
    without_logo = qr_service.generate_qr("one")
    assert without_logo != a  # логотип поверх кода реально рисуется


def test_qr_handles_long_payloads():
    png = qr_service.generate_qr("https://helper.savt.by/add/project?code=" + "x" * 500)

    assert Image.open(io.BytesIO(png)).width > 200


# --- Telegram ---

def test_deep_link_and_unknown_channel(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_username", "savt_bot")

    assert messenger_service.build_deep_link("telegram", "tok123") == "https://t.me/savt_bot?start=tok123"
    with pytest.raises(ValueError):
        messenger_service.build_deep_link("viber", "tok")


def test_webhook_secret_check(monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "tg-secret")
    assert messenger_service.verify_telegram_secret("tg-secret") is True
    assert messenger_service.verify_telegram_secret("other") is False
    assert messenger_service.verify_telegram_secret(None) is False
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")
    assert messenger_service.verify_telegram_secret("") is False


@pytest.fixture
def telegram(monkeypatch):
    sent = SimpleNamespace(posts=[], response=None, error=None)

    class Client:
        async def post(self, url, json):
            if sent.error:
                raise sent.error
            sent.posts.append((url, json))
            return sent.response or SimpleNamespace(
                headers={"content-type": "application/json"}, is_success=True, status_code=200,
                text="ok", json=lambda: {"ok": True},
            )

    monkeypatch.setattr(settings, "telegram_bot_token", "TOKEN")
    monkeypatch.setattr(messenger_service, "_get_client", lambda: Client())
    return sent


async def test_verification_code_goes_as_two_messages(telegram):
    await messenger_service.send_verification_code("telegram", "42", "123<456>")

    [(url1, first), (url2, second)] = telegram.posts
    assert url1 == "https://api.telegram.org/botTOKEN/sendMessage" == url2
    assert first["chat_id"] == "42" and first["reply_markup"] == {"remove_keyboard": True}
    assert second["text"] == "<code>123&lt;456&gt;</code>" and second["parse_mode"] == "HTML"  # код экранируется


async def test_contact_request_and_plain_messages(telegram):
    await messenger_service.send_contact_request("telegram", "42")
    await messenger_service.send_plain("telegram", "42", "Готово")

    [(_, contact), (_, plain)] = telegram.posts
    assert contact["reply_markup"]["keyboard"][0][0]["request_contact"] is True
    assert plain["text"] == "Готово" and plain["reply_markup"] == {"remove_keyboard": True}


@pytest.mark.parametrize("call", [
    lambda: messenger_service.send_verification_code("viber", "1", "1"),
    lambda: messenger_service.send_contact_request("viber", "1"),
    lambda: messenger_service.send_plain("viber", "1", "x"),
])
async def test_only_telegram_channel_is_supported(telegram, call):
    with pytest.raises(ValueError):
        await call()
    assert telegram.posts == []


async def test_telegram_failures_become_send_errors(telegram):
    telegram.error = httpx.ConnectError("нет сети")
    with pytest.raises(messenger_service.MessengerSendError):
        await messenger_service.send_plain("telegram", "42", "x")

    telegram.error = None
    telegram.response = SimpleNamespace(
        headers={"content-type": "application/json"}, is_success=False, status_code=403,
        text="Forbidden: bot was blocked by the user", json=lambda: {"ok": False},
    )
    with pytest.raises(messenger_service.MessengerSendError):
        await messenger_service.send_plain("telegram", "42", "x")

    telegram.response = SimpleNamespace(headers={"content-type": "text/html"}, is_success=True,
                                        status_code=200, text="<html>", json=lambda: {})
    with pytest.raises(messenger_service.MessengerSendError):  # ответ без ok=true тоже сбой
        await messenger_service.send_plain("telegram", "42", "x")


async def test_without_a_bot_token_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(messenger_service, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("сеть не нужна")))

    await messenger_service.send_plain("telegram", "42", "dev-режим")


# --- push ---

class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def fcm(monkeypatch, db_session):
    state = SimpleNamespace(sent=[], response=None)

    def send_each(messages):
        state.sent = messages
        return state.response or SimpleNamespace(
            success_count=len(messages), failure_count=0,
            responses=[SimpleNamespace(success=True, exception=None) for _ in messages],
        )

    monkeypatch.setattr(push_service, "is_firebase_ready", lambda: True)
    monkeypatch.setattr(push_service.messaging, "send_each", send_each)
    monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: _SessionContext(db_session))
    return state


async def _token(db_session, user, token):
    db_session.add(DeviceToken(user_id=user.id, token=token, platform="android"))
    await db_session.flush()


async def test_push_goes_to_every_device_of_the_user(db_session, fcm, make_user):
    user, other = await make_user(), await make_user()
    await _token(db_session, user, "t-1")
    await _token(db_session, user, "t-2")
    await _token(db_session, other, "t-other")

    await push_service.send_push(db_session, user.id, "Заголовок", "Текст", {"chat_id": 7}, "chat_message")

    assert sorted(m.token for m in fcm.sent) == ["t-1", "t-2"]
    assert fcm.sent[0].data == {"chat_id": "7"} and fcm.sent[0].notification.title == "Заголовок"


async def test_push_is_skipped_without_firebase_or_devices(db_session, fcm, make_user, monkeypatch):
    user = await make_user()

    await push_service.send_push(db_session, user.id, "a", "b")   # устройств нет
    assert fcm.sent == []

    await _token(db_session, user, "t-1")
    monkeypatch.setattr(push_service, "is_firebase_ready", lambda: False)
    await push_service.send_push(db_session, user.id, "a", "b")
    assert fcm.sent == []


async def test_disabled_switch_and_mute_block_the_push(db_session, fcm, make_user):
    user = await make_user()
    await _token(db_session, user, "t-1")
    settings_row = await NotificationRepository(db_session).ensure_settings(user.id)

    settings_row.chat_messages = False
    await push_service.send_push(db_session, user.id, "a", "b", notification_type="chat_message")
    assert fcm.sent == []
    await push_service.send_push(db_session, user.id, "a", "b", notification_type="request_status")
    assert len(fcm.sent) == 1                      # другие типы не затронуты
    fcm.sent = []
    await push_service.send_push(db_session, user.id, "a", "b", notification_type="operator_requested")
    assert len(fcm.sent) == 1                      # служебный сигнал настройками не выключается

    fcm.sent = []
    settings_row.muted_until = datetime.now(timezone.utc) + timedelta(hours=1)
    await push_service.send_push(db_session, user.id, "a", "b", notification_type="operator_requested")
    assert fcm.sent == []                          # пауза глушит всё


async def test_dead_tokens_are_removed_after_send(db_session, fcm, make_user):
    user = await make_user()
    await _token(db_session, user, "alive")
    await _token(db_session, user, "dead")
    fcm.response = SimpleNamespace(success_count=1, failure_count=1, responses=[
        SimpleNamespace(success=True, exception=None),
        SimpleNamespace(success=False, exception=Exception("registration-token-not-registered")),
    ])

    await push_service.send_push(db_session, user.id, "a", "b")

    left = (await db_session.execute(select(DeviceToken.token).where(DeviceToken.user_id == user.id))).scalars().all()
    assert left == ["alive"]


async def test_other_send_failures_keep_the_tokens(db_session, fcm, make_user):
    user = await make_user()
    await _token(db_session, user, "t-1")
    fcm.response = SimpleNamespace(success_count=0, failure_count=1, responses=[
        SimpleNamespace(success=False, exception=Exception("quota exceeded")),
    ])

    await push_service.send_push(db_session, user.id, "a", "b")

    assert (await db_session.execute(select(DeviceToken).where(DeviceToken.user_id == user.id))).first() is not None


async def test_fcm_crash_does_not_break_the_caller(db_session, fcm, make_user, monkeypatch):
    user = await make_user()
    await _token(db_session, user, "t-1")

    def boom(messages):
        raise RuntimeError("FCM недоступен")

    monkeypatch.setattr(push_service.messaging, "send_each", boom)

    await push_service.send_push(db_session, user.id, "a", "b")  # исключения быть не должно
