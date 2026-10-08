"""Регистрация через бота Telegram: /start с токеном, затем контакт. Номер
аккаунта берётся из контакта, который Telegram подтверждает сам, поэтому главное
здесь — отказ в чужих контактах и в занятых номерах. Telegram не вызывается:
ответы бота и отправка кода подменяются и записываются."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.messenger_link import MessengerLink
from app.models.role import Role
from app.models.user import User
from app.repositories.pending_registration import PendingRegistrationRepository
from app.services import messenger_service
from app.services import messenger_webhook_service as bot
from app.services.auth_service import PURPOSE_REGISTRATION, AuthService

CHAT = "5550001"
SENDER = 777


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def tg(db_session, monkeypatch):
    sent = SimpleNamespace(replies=[], contact_requests=[], codes=[], code_error=None, contact_error=None)
    monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: _SessionContext(db_session))

    async def send_plain(channel, chat_id, text):
        sent.replies.append((chat_id, text))

    async def send_contact_request(channel, chat_id):
        if sent.contact_error:
            raise sent.contact_error
        sent.contact_requests.append(chat_id)

    async def deliver_code_after_link(self, user_id, phone, purpose, channel, external_chat_id):
        if sent.code_error:
            raise sent.code_error
        sent.codes.append((user_id, phone, purpose, channel, external_chat_id))

    monkeypatch.setattr(messenger_service, "send_plain", send_plain)
    monkeypatch.setattr(messenger_service, "send_contact_request", send_contact_request)
    monkeypatch.setattr(AuthService, "deliver_code_after_link", deliver_code_after_link)
    return sent


async def _pending(db_session, token="tok-1", chat=None, **kw):
    pending = await PendingRegistrationRepository(db_session).create(
        token=token, hashed_password="hash", full_name=kw.get("full_name", "Иванов Иван"),
        user_type=kw.get("user_type", "individual"), organization_name=kw.get("organization_name"),
        contact_phone=kw.get("contact_phone", "+375290000000"),
        expires_at=kw.get("expires_at", datetime.now(timezone.utc) + timedelta(hours=1)),
    )
    if chat:
        pending.external_chat_id = chat
    await db_session.flush()
    return pending


def _contact_update(phone="375291234567", contact_user_id=SENDER, sender=SENDER, chat=CHAT):
    contact = {"phone_number": phone, "first_name": "Иван"}
    if contact_user_id is not None:
        contact["user_id"] = contact_user_id
    message = {"chat": {"id": int(chat)}, "contact": contact}
    if sender is not None:
        message["from"] = {"id": sender}
    return {"message": message}


async def _users_with_phone(db_session, phone):
    return list((await db_session.execute(select(User).where(User.phone == phone))).scalars().all())


# --- нормализация номера ---

@pytest.mark.parametrize("raw,expected", [
    ("375291234567", "+375291234567"),
    ("+375291234567", "+375291234567"),
    (" 375291234567 ", "+375291234567"),
    ("123", None),
    ("не номер", None),
    ("", None),
    (None, None),
])
def test_normalize_phone(raw, expected):
    assert bot._normalize_phone(raw) == expected


# --- шаг 1: /start ---

async def test_start_with_valid_token_remembers_chat_and_asks_for_contact(db_session, tg):
    pending = await _pending(db_session)

    await bot.handle_telegram_update({"message": {"chat": {"id": int(CHAT)}, "text": "/start tok-1"}})

    assert pending.external_chat_id == CHAT
    assert tg.contact_requests == [CHAT]
    assert tg.replies == []


@pytest.mark.parametrize("kind", ["unknown", "expired", "consumed"])
async def test_start_with_bad_token_says_registration_not_found(db_session, tg, kind):
    expires = datetime.now(timezone.utc) + (timedelta(hours=-1) if kind == "expired" else timedelta(hours=1))
    pending = await _pending(db_session, token="tok-x", expires_at=expires)
    if kind == "consumed":
        pending.consumed_at = datetime.now(timezone.utc)
    token = "tok-nope" if kind == "unknown" else "tok-x"

    await bot.handle_telegram_update({"message": {"chat": {"id": int(CHAT)}, "text": f"/start {token}"}})

    assert tg.contact_requests == []
    assert len(tg.replies) == 1 and "не найдена или истекла" in tg.replies[0][1]
    assert pending.external_chat_id is None


@pytest.mark.parametrize("payload", [
    {},
    {"message": {}},
    {"message": {"chat": {"id": 1}, "text": "привет"}},
    {"message": {"chat": {"id": 1}, "text": "/start"}},
    {"message": {"chat": {"id": 1}}},
])
async def test_irrelevant_updates_are_ignored(db_session, tg, payload):
    await bot.handle_telegram_update(payload)

    assert tg.replies == [] and tg.contact_requests == [] and tg.codes == []


async def test_failure_to_send_contact_request_does_not_crash(db_session, tg):
    await _pending(db_session)
    tg.contact_error = messenger_service.MessengerSendError("бот заблокирован")

    await bot.handle_telegram_update({"message": {"chat": {"id": int(CHAT)}, "text": "/start tok-1"}})  # не падает


# --- шаг 2: контакт ---

async def test_contact_creates_user_links_telegram_and_sends_code(db_session, tg):
    pending = await _pending(db_session, chat=CHAT, full_name="Сидоров Семён", user_type="organization",
                             organization_name="ООО Ромашка", contact_phone="+375291110000")

    await bot.handle_telegram_update(_contact_update("375291234567"))

    [user] = await _users_with_phone(db_session, "+375291234567")
    assert user.full_name == "Сидоров Семён"
    assert user.user_type == "organization" and user.organization_name == "ООО Ромашка"
    assert user.contact_phone == "+375291110000"
    assert user.hashed_password == "hash"
    assert user.is_phone_verified is False  # подтвердит только ввод кода
    assert user.is_active is True
    assert (await db_session.get(Role, user.role_id)).name == "user"
    assert pending.user_id == user.id and pending.failed_reason is None
    link = (await db_session.execute(select(MessengerLink).where(MessengerLink.user_id == user.id))).scalar_one()
    assert (link.channel, link.external_chat_id) == ("telegram", CHAT)
    assert tg.codes == [(user.id, "+375291234567", PURPOSE_REGISTRATION, "telegram", CHAT)]


async def test_contact_without_pending_registration_is_refused(db_session, tg):
    await bot.handle_telegram_update(_contact_update())

    assert await _users_with_phone(db_session, "+375291234567") == []
    assert len(tg.replies) == 1 and "не найдена или истекла" in tg.replies[0][1]


@pytest.mark.parametrize("contact_user_id,sender", [
    (999, SENDER),    # чужая карточка из адресной книги
    (None, SENDER),   # номера нет в Telegram, user_id отсутствует
    (SENDER, None),   # нет отправителя
])
async def test_foreign_contact_is_refused_and_registration_stays_open(db_session, tg, contact_user_id, sender):
    pending = await _pending(db_session, chat=CHAT)

    await bot.handle_telegram_update(_contact_update(contact_user_id=contact_user_id, sender=sender))

    assert await _users_with_phone(db_session, "+375291234567") == []
    assert pending.failed_reason == "foreign_contact"
    assert pending.consumed_at is None and pending.user_id is None
    assert "чужой контакт" in tg.replies[0][1]
    assert tg.codes == []


async def test_unparseable_phone_is_refused(db_session, tg):
    pending = await _pending(db_session, chat=CHAT)

    await bot.handle_telegram_update(_contact_update(phone="12"))

    assert pending.failed_reason == "bad_phone"
    assert tg.codes == []


async def test_already_registered_phone_is_refused(db_session, make_user, tg):
    owner = await make_user(phone="+375291234567", is_phone_verified=True)
    pending = await _pending(db_session, chat=CHAT)

    await bot.handle_telegram_update(_contact_update("375291234567"))

    assert pending.failed_reason == "phone_already_registered"
    assert await _users_with_phone(db_session, "+375291234567") == [owner]
    assert "уже зарегистрирован" in tg.replies[0][1]
    assert tg.codes == []


async def test_unfinished_registration_on_same_phone_is_overwritten_not_duplicated(db_session, make_user, tg):
    old = await make_user(phone="+375291234567", is_phone_verified=False, full_name="Старое имя")
    await _pending(db_session, chat=CHAT, full_name="Новое имя")

    await bot.handle_telegram_update(_contact_update("375291234567"))

    users = await _users_with_phone(db_session, "+375291234567")
    assert [u.id for u in users] == [old.id]
    assert users[0].full_name == "Новое имя" and users[0].hashed_password == "hash"
    assert len(tg.codes) == 1


async def test_telegram_already_linked_to_a_verified_account_is_refused(db_session, make_user, tg):
    owner = await make_user(phone="+375290001111", is_phone_verified=True)
    db_session.add(MessengerLink(user_id=owner.id, channel="telegram", external_chat_id=CHAT))
    pending = await _pending(db_session, chat=CHAT)
    await db_session.flush()

    # номер в Telegram с тех пор сменился — по телефону аккаунт не найти
    await bot.handle_telegram_update(_contact_update("375291234567"))

    assert pending.failed_reason == "telegram_already_linked"
    assert await _users_with_phone(db_session, "+375291234567") == []
    assert "+375290001111" in tg.replies[0][1]
    assert tg.codes == []


async def test_telegram_linked_only_to_unverified_account_does_not_block(db_session, make_user, tg):
    half = await make_user(phone="+375290001111", is_phone_verified=False)
    db_session.add(MessengerLink(user_id=half.id, channel="telegram", external_chat_id=CHAT))
    await _pending(db_session, chat=CHAT)
    await db_session.flush()

    await bot.handle_telegram_update(_contact_update("375291234567"))

    assert len(await _users_with_phone(db_session, "+375291234567")) == 1
    assert len(tg.codes) == 1


async def test_retry_after_refusal_succeeds_and_clears_the_reason(db_session, tg):
    pending = await _pending(db_session, chat=CHAT)
    await bot.handle_telegram_update(_contact_update(contact_user_id=999))
    assert pending.failed_reason == "foreign_contact"

    await bot.handle_telegram_update(_contact_update("375291234567"))

    assert pending.failed_reason is None
    assert len(await _users_with_phone(db_session, "+375291234567")) == 1
    assert len(tg.codes) == 1


async def test_code_delivery_failure_does_not_roll_back_the_account(db_session, tg):
    pending = await _pending(db_session, chat=CHAT)
    tg.code_error = RuntimeError("Telegram недоступен")

    await bot.handle_telegram_update(_contact_update("375291234567"))  # не падает

    [user] = await _users_with_phone(db_session, "+375291234567")
    assert pending.user_id == user.id
