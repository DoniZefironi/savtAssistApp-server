"""Отправка сообщения в чат (ChatService.send_message) — самая ветвистая функция
проекта: права, архив, идемпотентность по client_token, вложения, push владельцу,
дублирование в Bitrix для заявок и ответ бота. Внешнее подменяется и
записывается: realtime-события, push, комментарий в Bitrix, бот, распознавание
голоса и фото. Фоновые задачи (spawn) не запускаются сами — тест выполняет их
явно через env.run_background()."""
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.message import Message
from app.models.service_request import ServiceRequest
from app.schemas.chat import AttachmentIn, MessageCreateIn
from app.services import bot_service, chat_service, push_service, service_request_service
from app.services.chat_service import ChatService


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def env(db_session, monkeypatch):
    e = SimpleNamespace(
        events=[], pushes=[], bitrix=[], bot_calls=[], spawned=[],
        transcript=None, image_description=None, transcribed=[], analyzed=[], bot_error=None,
    )

    async def created(chat_id, message):
        e.events.append(("message.created", chat_id, message))

    async def updated(chat_id, summary):
        e.events.append(("chat.updated", chat_id, summary))

    async def send_push(session, user_id, title, body, data=None, notification_type=None):
        e.pushes.append({"user_id": user_id, "title": title, "body": body, "data": data, "type": notification_type})

    def sync_message_to_bitrix(service_request_id, sender_name, text, attachment_urls):
        e.bitrix.append((service_request_id, sender_name, text, attachment_urls))

    def spawn(coro, **kwargs):
        e.spawned.append(coro)

    async def handle_message(session, chat_id, text):
        if e.bot_error:
            raise e.bot_error
        e.bot_calls.append((chat_id, text))

    async def transcribe(file_url):
        e.transcribed.append(file_url)
        return e.transcript

    async def analyze(file_url, mime_type):
        e.analyzed.append(file_url)
        return e.image_description

    async def run_background():
        pending, e.spawned = e.spawned, []
        for coro in pending:
            await coro

    e.run_background = run_background
    monkeypatch.setattr(chat_service, "publish_message_created", created)
    monkeypatch.setattr(chat_service, "publish_chat_updated", updated)
    monkeypatch.setattr(chat_service, "spawn", spawn)
    monkeypatch.setattr(chat_service, "_transcribe_voice_attachment", transcribe)
    monkeypatch.setattr(chat_service, "_analyze_image_attachment", analyze)
    monkeypatch.setattr(push_service, "send_push", send_push)
    monkeypatch.setattr(service_request_service, "sync_message_to_bitrix", sync_message_to_bitrix)
    monkeypatch.setattr(bot_service, "handle_message", handle_message)
    monkeypatch.setattr("app.core.signed_urls.sign_url_long", lambda url: f"long:{url}")
    monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: _SessionContext(db_session))
    yield e
    for coro in e.spawned:
        coro.close()


def text_msg(text="Не работает насос", **kw):
    return MessageCreateIn(text=text, **kw)


def file_att(url="/static/files/a.pdf", mime="application/pdf", name="a.pdf", **kw):
    return AttachmentIn(file_url=url, file_name=name, file_size_bytes=100, mime_type=mime, **kw)


async def send(db_session, chat, sender, data, **kw):
    return await ChatService(db_session).send_message(chat.id, sender.id, data, **kw)


async def _messages(db_session, chat):
    return list((await db_session.execute(select(Message).where(Message.chat_id == chat.id))).scalars().all())


# --- права и состояние чата ---

async def test_owner_sends_text(db_session, make_user, make_chat, env):
    owner = await make_user(full_name="Иванов Иван")
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    result = await send(db_session, chat, owner, text_msg())

    assert result.text == "Не работает насос"
    assert result.sender_id == owner.id and result.sender_name == "Иванов Иван"
    assert len(await _messages(db_session, chat)) == 1
    assert chat.last_message_at is not None
    assert chat.last_user_message_at == chat.last_message_at


async def test_operator_can_write_to_users_chat_without_touching_user_activity(db_session, make_user, make_chat, env):
    owner = await make_user()
    operator = await make_user("operator", full_name="Оператор Олег")
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    await send(db_session, chat, operator, text_msg("Принято в работу"))

    assert chat.last_message_at is not None
    assert chat.last_user_message_at is None  # таймер молчания пользователя не сбрасывается


async def test_stranger_cannot_write(db_session, make_user, make_chat, env):
    owner = await make_user()
    stranger = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    with pytest.raises(PermissionDeniedError):
        await send(db_session, chat, stranger, text_msg())

    assert await _messages(db_session, chat) == []


async def test_missing_chat_is_not_found(db_session, make_user, env):
    user = await make_user()

    with pytest.raises(NotFoundError):
        await ChatService(db_session).send_message(987654, user.id, text_msg())


async def test_operator_cannot_write_to_personal_notes(db_session, make_user, make_chat, env):
    owner = await make_user()
    operator = await make_user("operator")
    notes = await make_chat(owner, chat_type="notes", bot_active=False)

    with pytest.raises(PermissionDeniedError):
        await send(db_session, notes, operator, text_msg())


async def test_archived_chat_rejects_messages(db_session, make_user, make_chat, env):
    from datetime import datetime, timezone
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False, archived_at=datetime.now(timezone.utc))

    with pytest.raises(PermissionDeniedError):
        await send(db_session, chat, owner, text_msg())

    assert await _messages(db_session, chat) == []
    assert env.events == []


# --- идемпотентность ---

async def test_repeated_client_token_returns_the_same_message(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    first = await send(db_session, chat, owner, text_msg(client_token="tmp-1"))
    second = await send(db_session, chat, owner, text_msg("Другой текст", client_token="tmp-1"))

    assert second.id == first.id and second.text == "Не работает насос"
    assert len(await _messages(db_session, chat)) == 1
    # повтор не рассылает событий и не шлёт push второй раз
    assert [e[0] for e in env.events].count("message.created") == 1


async def test_same_client_token_from_different_senders_are_different_messages(db_session, make_user, make_chat, env):
    owner = await make_user()
    operator = await make_user("operator")
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    a = await send(db_session, chat, owner, text_msg("От клиента", client_token="same"))
    b = await send(db_session, chat, operator, text_msg("От оператора", client_token="same"))

    assert a.id != b.id


async def test_messages_without_token_are_never_deduplicated(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    await send(db_session, chat, owner, text_msg())
    await send(db_session, chat, owner, text_msg())

    assert len(await _messages(db_session, chat)) == 2


# --- вложения и ответы ---

async def test_attachment_types_follow_mime(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False)
    data = MessageCreateIn(attachments=[
        file_att("/static/a.jpg", "image/jpeg", "a.jpg"),
        file_att("/static/b.mp4", "video/mp4", "b.mp4"),
        file_att("/static/c.ogg", "audio/ogg", "c.ogg", duration_seconds=7),
        file_att("/static/d.pdf", "application/pdf", "d.pdf"),
        AttachmentIn(latitude=53.9, longitude=27.5),
    ])

    result = await send(db_session, chat, owner, data)

    assert [a.attachment_type for a in result.attachments] == ["image", "video", "voice", "document", "location"]
    location = result.attachments[-1]
    assert (location.latitude, location.longitude) == (53.9, 27.5) and location.file_url is None
    assert result.attachments[2].duration_seconds == 7
    assert result.text is None


async def test_reply_to_is_stored(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False)
    first = await send(db_session, chat, owner, text_msg("Вопрос"))

    reply = await send(db_session, chat, owner, text_msg("Уточнение", reply_to_message_id=first.id))

    assert reply.reply_to_message_id == first.id


# --- realtime ---

async def test_events_are_published_after_save(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    result = await send(db_session, chat, owner, text_msg("Привет"))

    kinds = [e[0] for e in env.events]
    assert kinds == ["message.created", "chat.updated"]
    assert env.events[0][2]["id"] == result.id and env.events[0][2]["text"] == "Привет"
    assert env.events[1][2]["last_message_text"] == "Привет" and env.events[1][1] == chat.id


# --- push владельцу ---

async def test_operator_message_pushes_the_owner(db_session, make_user, make_chat, env):
    owner = await make_user()
    operator = await make_user("operator", full_name="Оператор Олег")
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    await send(db_session, chat, operator, text_msg("Я" * 300))

    [push] = env.pushes
    assert push["user_id"] == owner.id and push["title"] == "Оператор Олег"
    assert push["body"] == "Я" * 100
    assert push["data"] == {"chat_id": str(chat.id), "type": "chat_message"}
    assert push["type"] == "chat_message"


async def test_attachment_only_message_pushes_a_placeholder(db_session, make_user, make_chat, env):
    owner = await make_user()
    operator = await make_user("operator")
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    await send(db_session, chat, operator, MessageCreateIn(attachments=[file_att()]))

    assert env.pushes[0]["body"] == "Вложение"


async def test_owner_message_does_not_push_the_owner(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    await send(db_session, chat, owner, text_msg())

    assert env.pushes == []


# --- заявки: дублирование в Bitrix ---

async def _service_chat(db_session, owner, make_project, make_chat):
    project = await make_project()
    sr = ServiceRequest(
        user_id=owner.id, project_id=project.id, request_type="repair",
        is_under_warranty=False, description="Течёт насос",
    )
    db_session.add(sr)
    await db_session.flush()
    chat = await make_chat(owner, chat_type="service_request", project_id=project.id,
                           service_request_id=sr.id, bot_active=False)
    return chat, sr


async def test_service_request_message_is_mirrored_to_bitrix(db_session, make_user, make_project, make_chat, env):
    owner = await make_user(full_name="Иванов Иван")
    chat, sr = await _service_chat(db_session, owner, make_project, make_chat)

    await send(db_session, chat, owner, MessageCreateIn(text="Фото в комментарий", attachments=[file_att("/static/p.jpg", "image/jpeg", "p.jpg")]))

    assert env.bitrix == [(sr.id, "Иванов Иван", "Фото в комментарий", ["long:/static/p.jpg"])]


async def test_operator_reply_in_service_request_is_mirrored_too(db_session, make_user, make_project, make_chat, env):
    owner = await make_user()
    operator = await make_user("operator", full_name="Оператор Олег")
    chat, sr = await _service_chat(db_session, owner, make_project, make_chat)

    await send(db_session, chat, operator, text_msg("Приедем завтра"))

    assert env.bitrix == [(sr.id, "Оператор Олег", "Приедем завтра", [])]


async def test_message_that_came_from_bitrix_is_not_sent_back(db_session, make_user, make_project, make_chat, env):
    owner = await make_user()
    chat, _ = await _service_chat(db_session, owner, make_project, make_chat)

    await send(db_session, chat, owner, text_msg("Из Bitrix"), sync_to_bitrix=False)

    assert env.bitrix == []


async def test_other_chat_types_are_not_mirrored(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support", bot_active=False)

    await send(db_session, chat, owner, text_msg())

    assert env.bitrix == []


# --- бот ---

async def test_owner_message_triggers_the_bot(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support")  # bot_active по умолчанию

    await send(db_session, chat, owner, text_msg("Не включается панель"))
    assert env.bot_calls == []  # отвечает в фоне, не внутри запроса
    await env.run_background()

    assert env.bot_calls == [(chat.id, "Не включается панель")]


@pytest.mark.parametrize("scenario", ["bot_off", "notes", "operator_sender", "service_request"])
async def test_bot_stays_silent(db_session, make_user, make_project, make_chat, env, scenario):
    owner = await make_user()
    operator = await make_user("operator")
    sender = owner
    if scenario == "bot_off":
        chat = await make_chat(owner, chat_type="support", bot_active=False)
    elif scenario == "notes":
        chat = await make_chat(owner, chat_type="notes")
    elif scenario == "operator_sender":
        chat = await make_chat(owner, chat_type="support")
        sender = operator
    else:
        chat, _ = await _service_chat(db_session, owner, make_project, make_chat)
        chat.bot_active = True  # даже при включённом боте в чатах заявок отвечает человек

    await send(db_session, chat, sender, text_msg())
    await env.run_background()

    assert env.bot_calls == []


async def test_voice_without_text_is_transcribed_for_the_bot(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support")
    env.transcript = "не горит индикатор"

    await send(db_session, chat, owner, MessageCreateIn(attachments=[file_att("/static/v.ogg", "audio/ogg", "v.ogg")]))
    await env.run_background()

    assert env.transcribed == ["/static/v.ogg"]
    assert env.bot_calls == [(chat.id, "не горит индикатор")]


async def test_voice_that_cannot_be_transcribed_gives_the_bot_nothing(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support")
    env.transcript = None

    await send(db_session, chat, owner, MessageCreateIn(attachments=[file_att("/static/v.ogg", "audio/ogg", "v.ogg")]))
    await env.run_background()

    assert env.bot_calls == []


async def test_voice_is_not_transcribed_when_there_is_text(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support")

    await send(db_session, chat, owner, MessageCreateIn(text="Послушайте", attachments=[file_att("/static/v.ogg", "audio/ogg", "v.ogg")]))
    await env.run_background()

    assert env.transcribed == []
    assert env.bot_calls == [(chat.id, "Послушайте")]


async def test_photos_are_described_and_added_to_the_text(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support")
    env.image_description = "на экране код E-12"

    await send(db_session, chat, owner, MessageCreateIn(
        text="Вот что показывает", attachments=[file_att("/static/p.jpg", "image/jpeg", "p.jpg")],
    ))
    await env.run_background()

    assert env.bot_calls == [(chat.id, "Вот что показывает\n\n[Фото: на экране код E-12]")]


async def test_at_most_three_photos_are_analyzed(db_session, make_user, make_chat, env):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support")
    env.image_description = "фото"

    await send(db_session, chat, owner, MessageCreateIn(attachments=[
        file_att(f"/static/{i}.jpg", "image/jpeg", f"{i}.jpg") for i in range(5)
    ]))
    await env.run_background()

    assert len(env.analyzed) == 3
    assert env.bot_calls[0][1].count("[Фото:") == 3


async def test_bot_failure_is_logged_and_does_not_break_the_message(db_session, make_user, make_chat, env, caplog):
    owner = await make_user()
    chat = await make_chat(owner, chat_type="support")
    env.bot_error = RuntimeError("Yandex недоступен")

    with caplog.at_level(logging.ERROR):
        result = await send(db_session, chat, owner, text_msg())
        await env.run_background()  # не падает

    assert result.text == "Не работает насос"
    assert "Bot reply failed" in caplog.text
