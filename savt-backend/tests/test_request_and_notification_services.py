"""Заявки на регистрацию и на сброс пароля (подача, список, одобрение, отказ) и
сервис уведомлений (запись с учётом настроек, пауза, устройства, рассылка).
Push и realtime подменяются и записываются."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.exceptions import AlreadyExistsError, AuthenticationError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.chat import Chat
from app.models.device_token import DeviceToken
from app.models.notification import Notification
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    ApproveRegistrationRequestIn,
    PasswordResetRequestCreateIn,
    RegistrationRequestCreateIn,
)
from app.schemas.notifications import BroadcastIn, DeviceTokenIn, NotificationSettingsPatchIn
from app.schemas.requests import RejectRequestIn
from app.services import notification_service, realtime_events
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.services.password_reset_request_service import PasswordResetRequestService
from app.services.registration_request_service import RegistrationRequestService


@pytest.fixture
def env(monkeypatch):
    e = SimpleNamespace(pushes=[], chats=[])

    async def push(session, user_id, title, body, data=None, notification_type=None):
        e.pushes.append((user_id, title, notification_type))

    async def chat_created(chat_id, summary):
        e.chats.append(chat_id)

    monkeypatch.setattr(notification_service, "send_push", push)
    monkeypatch.setattr(realtime_events, "publish_chat_created", chat_created)
    return e


async def _audit(db_session, action):
    return list((await db_session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _notes(db_session, user):
    return list((await db_session.execute(select(Notification).where(Notification.user_id == user.id))).scalars())


def _registration(**kw):
    data = dict(phone="+375291234567", password="password8", password_confirm="password8",
                full_name="Иванов Иван", user_type="individual")
    data.update(kw)
    return RegistrationRequestCreateIn(**data)


# --- заявки на регистрацию ---

async def test_registration_submit_and_approve_creates_a_working_account(db_session, env, make_user):
    admin = await make_user("admin")
    svc = RegistrationRequestService(db_session)

    out = await svc.submit(_registration(user_type="organization", organization_name="ООО Ромашка",
                                         contact_phone="+375291110000"))
    await svc.approve(out.id, ApproveRegistrationRequestIn(admin_response="Добро пожаловать"), admin.id, "admin")

    user = (await db_session.execute(select(User).where(User.phone == "+375291234567"))).scalar_one()
    assert (user.full_name, user.organization_name, user.contact_phone) == ("Иванов Иван", "ООО Ромашка", "+375291110000")
    assert user.is_active and user.is_verified and user.is_phone_verified
    chats = {c.chat_type for c in (await db_session.execute(select(Chat).where(Chat.user_id == user.id))).scalars()}
    assert {"support", "notes"} <= chats and len(env.chats) == 1
    # тот пароль, что человек ввёл при подаче, сразу работает
    await AuthService(db_session).login("+375291234567", "password8", None, None)
    [entry] = await _audit(db_session, "registration_request.approve")
    assert entry.payload["created_user_id"] == user.id
    assert len(await _audit(db_session, "registration_request.create")) == 1

    page = await svc.list_requests(status="approved")
    [item] = page.items
    assert item.resolved_by_admin_name == admin.full_name and item.status == "approved"


async def test_registration_submit_rejects_duplicates(db_session, env, make_user):
    await make_user(phone="+375291111111")
    svc = RegistrationRequestService(db_session)

    with pytest.raises(AlreadyExistsError):
        await svc.submit(_registration(phone="+375291111111"))
    await svc.submit(_registration(phone="+375292222222"))
    with pytest.raises(AlreadyExistsError):
        await svc.submit(_registration(phone="+375292222222"))


async def test_registration_can_be_resubmitted_after_rejection(db_session, env, make_user):
    admin = await make_user("admin")
    svc = RegistrationRequestService(db_session)
    first = await svc.submit(_registration())

    await svc.reject(first.id, RejectRequestIn(admin_response="Нет данных"), admin.id, "admin")
    second = await svc.submit(_registration())

    assert second.id != first.id
    [entry] = await _audit(db_session, "registration_request.reject")
    assert entry.payload["reason"] == "Нет данных"


async def test_registration_decisions_are_final(db_session, env, make_user):
    admin = await make_user("admin")
    svc = RegistrationRequestService(db_session)
    out = await svc.submit(_registration())
    await svc.reject(out.id, RejectRequestIn(admin_response="Нет"), admin.id, "admin")

    with pytest.raises(AlreadyExistsError):
        await svc.approve(out.id, ApproveRegistrationRequestIn(), admin.id, "admin")
    with pytest.raises(AlreadyExistsError):
        await svc.reject(out.id, RejectRequestIn(admin_response="Ещё раз"), admin.id, "admin")
    for call in (
        lambda: svc.approve(999999, ApproveRegistrationRequestIn(), admin.id, "admin"),
        lambda: svc.reject(999999, RejectRequestIn(admin_response="x"), admin.id, "admin"),
    ):
        with pytest.raises(NotFoundError):
            await call()


async def test_registration_approve_fails_if_phone_was_taken_meanwhile(db_session, env, make_user):
    admin = await make_user("admin")
    svc = RegistrationRequestService(db_session)
    out = await svc.submit(_registration(phone="+375293333333"))
    await make_user(phone="+375293333333")

    with pytest.raises(AlreadyExistsError):
        await svc.approve(out.id, ApproveRegistrationRequestIn(), admin.id, "admin")


async def test_registration_list_filters_and_search(db_session, env):
    svc = RegistrationRequestService(db_session)
    await svc.submit(_registration(phone="+375291000001", full_name="Петров Пётр"))
    await svc.submit(_registration(phone="+375291000002", full_name="Сидоров Семён", user_type="organization",
                                   organization_name="ООО Север"))

    by_name = await svc.list_requests(search="Петров")
    by_org = await svc.list_requests(search="Север")
    pending = await svc.list_requests(status="pending")
    nothing = await svc.list_requests(status="rejected")

    assert [i.full_name for i in by_name.items] == ["Петров Пётр"]
    assert [i.full_name for i in by_org.items] == ["Сидоров Семён"]
    assert pending.total == 2 and nothing.total == 0


def test_registration_schema_rules():
    for bad in (
        dict(password_confirm="другой пароль"),
        dict(user_type="alien"),
        dict(user_type="organization"),  # организации нужно название
    ):
        with pytest.raises(ValueError):
            _registration(**bad)


# --- заявки на сброс пароля ---

@pytest.fixture
def make_client(make_user):
    """Клиент с номером, который проходит проверку формата в схемах заявок."""
    counter = {"n": 0}

    async def _make(role="user", **kw):
        counter["n"] += 1
        kw.setdefault("phone", f"+37529110{counter['n']:04d}")
        return await make_user(role, **kw)
    return _make


def _reset(phone, password="newpass88", comment=None):
    return PasswordResetRequestCreateIn(phone=phone, new_password=password, new_password_confirm=password,
                                        user_comment=comment)


async def test_password_reset_approve_applies_password_and_revokes_sessions(db_session, env, make_client):
    admin = await make_client("admin")
    user = await make_client(password="oldpass88")
    await AuthService(db_session).login(user.phone, "oldpass88", None, None)
    svc = PasswordResetRequestService(db_session)

    out = await svc.submit(_reset(user.phone, comment="Потерял Telegram"))
    # пока админ не одобрил, старый пароль работает
    await AuthService(db_session).login(user.phone, "oldpass88", None, None)
    await svc.approve(out.id, "Проверили по телефону", admin.id, "admin")

    with pytest.raises(AuthenticationError):
        await AuthService(db_session).login(user.phone, "oldpass88", None, None)
    await AuthService(db_session).login(user.phone, "newpass88", None, None)
    tokens = (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))).scalars().all()
    assert any(t.revoked_at is not None for t in tokens)
    [note] = await _notes(db_session, user)
    assert note.title == "Пароль изменён" and env.pushes[-1][0] == user.id
    assert len(await _audit(db_session, "password_reset_request.create")) == 1
    assert len(await _audit(db_session, "password_reset_request.approve")) == 1


async def test_password_reset_reject_notifies_with_reason(db_session, env, make_client):
    admin = await make_client("admin")
    user = await make_client(password="oldpass88")
    svc = PasswordResetRequestService(db_session)
    out = await svc.submit(_reset(user.phone))

    await svc.reject(out.id, RejectRequestIn(admin_response="Не удалось подтвердить личность"), admin.id, "admin")

    await AuthService(db_session).login(user.phone, "oldpass88", None, None)  # пароль не менялся
    [note] = await _notes(db_session, user)
    assert note.body == "Не удалось подтвердить личность"
    [entry] = await _audit(db_session, "password_reset_request.reject")
    assert entry.payload["reason"] == "Не удалось подтвердить личность"


async def test_password_reset_submit_rules(db_session, env, make_client):
    active = await make_client()
    blocked = await make_client(is_active=False)
    svc = PasswordResetRequestService(db_session)

    with pytest.raises(NotFoundError):
        await svc.submit(_reset("+375299999999"))
    with pytest.raises(NotFoundError):
        await svc.submit(_reset(blocked.phone))
    await svc.submit(_reset(active.phone))
    with pytest.raises(AlreadyExistsError):
        await svc.submit(_reset(active.phone))


async def test_password_reset_decisions_are_final_and_checked(db_session, env, make_client):
    admin = await make_client("admin")
    user = await make_client()
    svc = PasswordResetRequestService(db_session)
    out = await svc.submit(_reset(user.phone))
    await svc.approve(out.id, None, admin.id, "admin")

    with pytest.raises(AlreadyExistsError):
        await svc.approve(out.id, None, admin.id, "admin")
    with pytest.raises(AlreadyExistsError):
        await svc.reject(out.id, RejectRequestIn(admin_response="x"), admin.id, "admin")
    with pytest.raises(NotFoundError):
        await svc.approve(999999, None, admin.id, "admin")
    with pytest.raises(NotFoundError):
        await svc.reject(999999, RejectRequestIn(admin_response="x"), admin.id, "admin")


async def test_password_reset_list_shows_resolver_name(db_session, env, make_client):
    admin = await make_client("admin", full_name="Админ Анна")
    user = await make_client(full_name="Клиент")
    svc = PasswordResetRequestService(db_session)
    out = await svc.submit(_reset(user.phone))
    await svc.approve(out.id, None, admin.id, "admin")

    page = await svc.list_requests(status="approved", search="Клиент")

    [item] = page.items
    assert item.resolved_by_admin_name == "Админ Анна" and item.user_phone == user.phone


# --- уведомления: запись, чтение ---

async def test_send_stores_and_pushes(db_session, env, make_user):
    user = await make_user()

    await NotificationService(db_session).send(user.id, "request_status", "Заголовок", "Текст", {"k": 1})

    [note] = await _notes(db_session, user)
    assert (note.type, note.title, note.data) == ("request_status", "Заголовок", {"k": 1})
    assert env.pushes == [(user.id, "Заголовок", "request_status")]


@pytest.mark.parametrize("type_,field", [
    ("chat_message", "chat_messages"),
    ("request_status", "request_status_change"),
    ("warranty_expiring", "warranty_expiring"),
    ("promotional", "promotional"),
    ("cabinet_alarm", "cabinet_alarms"),
])
async def test_disabled_type_is_neither_stored_nor_pushed(db_session, env, make_user, type_, field):
    user = await make_user()
    svc = NotificationService(db_session)
    await svc.update_settings(user.id, NotificationSettingsPatchIn(**{field: False}))

    await svc.send(user.id, type_, "Тема", "Текст")

    assert await _notes(db_session, user) == [] and env.pushes == []


async def test_list_filters_and_read_state(db_session, env, make_user):
    user, other = await make_user(), await make_user()
    svc = NotificationService(db_session)
    await svc.send(user.id, "request_status", "Первое", "а")
    await svc.send(user.id, "promotional", "Второе", "б")
    await svc.send(other.id, "request_status", "Чужое", "в")
    first = (await svc.list_notifications(user.id, None, 1, 20, types=["request_status"])).items[0]

    assert (await svc.unread_count(user.id)) == 2
    await svc.mark_read(user.id, first.id)
    assert (await svc.unread_count(user.id)) == 1
    unread = await svc.list_notifications(user.id, False, 1, 20)
    assert [n.title for n in unread.items] == ["Второе"]
    assert (await svc.list_notifications(user.id, None, 1, 20)).total == 2

    await svc.mark_all_read(user.id)
    assert (await svc.unread_count(user.id)) == 0
    await svc.delete_all(user.id)
    assert (await svc.list_notifications(user.id, None, 1, 20)).total == 0
    assert (await svc.unread_count(other.id)) == 1  # чужие не тронуты


async def test_mark_read_of_foreign_or_missing_notification(db_session, env, make_user):
    user, other = await make_user(), await make_user()
    svc = NotificationService(db_session)
    await svc.send(other.id, "request_status", "Чужое", "в")
    foreign = (await svc.list_notifications(other.id, None, 1, 20)).items[0]

    with pytest.raises(NotFoundError):
        await svc.mark_read(user.id, foreign.id)
    with pytest.raises(NotFoundError):
        await svc.mark_read(user.id, 999999)


# --- настройки и пауза ---

async def test_settings_defaults_and_patch(db_session, env, make_user):
    user = await make_user()
    svc = NotificationService(db_session)

    defaults = await svc.get_settings(user.id)
    changed = await svc.update_settings(user.id, NotificationSettingsPatchIn(promotional=False))

    assert defaults.chat_messages is True and defaults.is_muted is False
    assert changed.promotional is False and changed.chat_messages is True


async def test_mute_for_hours_and_indefinitely(db_session, env, make_user):
    user = await make_user()
    svc = NotificationService(db_session)

    hourly = await svc.mute(user.id, 2)
    assert hourly.is_muted and hourly.muted_indefinitely is False
    assert timedelta(minutes=118) < hourly.muted_until - datetime.now(timezone.utc) <= timedelta(hours=2)

    forever = await svc.mute(user.id, None)
    assert forever.is_muted and forever.muted_indefinitely and forever.muted_until is None

    back = await svc.unmute(user.id)
    assert back.is_muted is False and back.muted_until is None


async def test_mute_is_measured_from_now_not_extended(db_session, env, make_user):
    user = await make_user()
    svc = NotificationService(db_session)
    await svc.mute(user.id, 10)

    short = await svc.mute(user.id, 1)

    assert short.muted_until - datetime.now(timezone.utc) <= timedelta(hours=1)


async def test_expired_mute_stops_muting(db_session, env, make_user):
    user = await make_user()
    svc = NotificationService(db_session)
    settings = await svc.repo.ensure_settings(user.id)
    settings.muted_until = datetime.now(timezone.utc) - timedelta(minutes=1)

    assert (await svc.get_settings(user.id)).is_muted is False


# --- устройства ---

async def test_device_tokens(db_session, env, make_user):
    user, other = await make_user(), await make_user()
    svc = NotificationService(db_session)

    await svc.register_device(user.id, DeviceTokenIn(token="tok-1", platform="android"))
    await svc.register_device(user.id, DeviceTokenIn(token="tok-1", platform="android"))  # повтор — без дубля
    await svc.register_device(other.id, DeviceTokenIn(token="tok-1", platform="ios"))     # устройство сменило владельца
    rows = (await db_session.execute(select(DeviceToken).where(DeviceToken.token == "tok-1"))).scalars().all()
    assert len(rows) == 1 and rows[0].user_id == other.id and rows[0].platform == "ios"

    with pytest.raises(NotFoundError):
        await svc.remove_device(user.id, "tok-1")  # чужой токен не удалить
    await svc.remove_device(other.id, "tok-1")
    with pytest.raises(NotFoundError):
        await svc.remove_device(other.id, "tok-1")


# --- рассылка ---

async def test_broadcast_respects_opt_out_and_role(db_session, env, make_user):
    admin = await make_user("admin")
    wants, declined, operator = await make_user(), await make_user(), await make_user("operator")
    blocked = await make_user(is_active=False)
    svc = NotificationService(db_session)
    await svc.update_settings(declined.id, NotificationSettingsPatchIn(promotional=False))

    result = await svc.broadcast(BroadcastIn(title="Акция", body="Скидка", role="user"), admin.id, "admin")

    assert result.sent_to >= 1 and result.skipped_opted_out == 1
    assert len(await _notes(db_session, wants)) == 1
    for silent in (declined, operator, blocked):
        assert await _notes(db_session, silent) == []
    assert (wants.id, "Акция", "promotional") in env.pushes
    [entry] = await _audit(db_session, "notification.broadcast")
    assert entry.payload["role"] == "user" and entry.payload["opted_out"] == 1
    assert entry.payload["recipients"] == result.sent_to
