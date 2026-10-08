"""Авторизация вокруг входа: регистрация по коду, сброс пароля, смена пароля,
профиль, удаление аккаунта. Telegram не вызывается: отправка кода подменяется и
записывается, чтобы тест мог «ввести» настоящий код."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.exceptions import (
    AlreadyExistsError, AuthenticationError, InvalidCodeError, NotFoundError, RateLimitError,
)
from app.core.security import hash_password, hash_token, verify_password
from app.models.chat import Chat
from app.models.messenger_link import MessengerLink
from app.models.phone_verification_code import PhoneVerificationCode
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.auth import PhoneCodeRepository
from app.repositories.pending_registration import PendingRegistrationRepository
from app.services import messenger_service
from app.services.auth_service import PURPOSE_PASSWORD_RESET, PURPOSE_REGISTRATION, AuthService

PHONE = "+375291234567"


@pytest.fixture
def telegram(monkeypatch):
    box = SimpleNamespace(codes=[], fail=False)

    async def send_verification_code(channel, chat_id, code):
        if box.fail:
            raise messenger_service.MessengerSendError("бот заблокирован")
        box.codes.append((chat_id, code))

    monkeypatch.setattr(messenger_service, "send_verification_code", send_verification_code)
    return box


@pytest.fixture
def auth(db_session):
    return AuthService(db_session)


async def _code(db_session, phone, purpose, code="123456", expires_in=timedelta(minutes=10), attempts=0):
    obj = await PhoneCodeRepository(db_session).create(
        phone=phone, code_hash=hash_token(code), purpose=purpose,
        expires_at=datetime.now(timezone.utc) + expires_in, max_attempts=5,
    )
    obj.attempts = attempts
    await db_session.flush()
    return obj


async def _link(db_session, user, chat="9001"):
    db_session.add(MessengerLink(user_id=user.id, channel="telegram", external_chat_id=chat))
    await db_session.flush()


async def _codes(db_session, phone, purpose):
    return list((await db_session.execute(
        select(PhoneVerificationCode).where(
            PhoneVerificationCode.phone == phone, PhoneVerificationCode.purpose == purpose,
        )
    )).scalars().all())


async def _pending(db_session, token="reg-token", **kw):
    return await PendingRegistrationRepository(db_session).create(
        token=token, hashed_password=hash_password("password8"), full_name="Иванов Иван",
        user_type="individual", organization_name=None, contact_phone=None,
        expires_at=kw.get("expires_at", datetime.now(timezone.utc) + timedelta(hours=1)),
    )


async def _active_sessions(db_session, user):
    rows = (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))).scalars().all()
    return [t for t in rows if t.revoked_at is None]


# --- регистрация: старт и статус ---

async def test_register_start_parks_the_form_and_returns_a_deep_link(auth, db_session):
    token, deep_link, cooldown = await auth.register_start(
        "password8", "Иванов Иван", "individual", None, None,
    )

    pending = await PendingRegistrationRepository(db_session).find_by_token(token)
    assert pending is not None and pending.user_id is None
    assert verify_password("password8", pending.hashed_password)
    assert token in deep_link
    assert cooldown > 0


async def test_register_status_follows_the_registration(auth, db_session):
    with pytest.raises(NotFoundError):
        await auth.register_status("нет-такой")

    pending = await _pending(db_session)
    assert await auth.register_status("reg-token") == ("waiting_contact", None)

    pending.failed_reason = "foreign_contact"
    assert await auth.register_status("reg-token") == ("failed", "foreign_contact")

    pending.failed_reason = None
    pending.user_id = (await _user(db_session)).id
    assert await auth.register_status("reg-token") == ("code_sent", None)

    pending.consumed_at = datetime.now(timezone.utc)
    assert await auth.register_status("reg-token") == ("completed", None)


async def test_register_status_expired(auth, db_session):
    await _pending(db_session, token="old", expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    assert await auth.register_status("old") == ("expired", None)


async def _user(db_session, **kw):
    user = User(
        phone=kw.get("phone", PHONE), full_name="Иванов Иван", hashed_password=hash_password("password8"),
        role_id=1, is_phone_verified=kw.get("verified", False), is_verified=True, is_active=kw.get("active", True),
    )
    db_session.add(user)
    await db_session.flush()
    return user


# --- регистрация: завершение кодом ---

async def test_register_complete_with_the_right_code(auth, db_session):
    user = await _user(db_session)
    pending = await _pending(db_session)
    pending.user_id = user.id
    await _code(db_session, PHONE, PURPOSE_REGISTRATION)

    access, refresh = await auth.register_complete("reg-token", "123456", None, None)

    assert access and refresh and access != refresh
    assert user.is_phone_verified is True
    assert pending.consumed_at is not None
    chats = {c.chat_type for c in (await db_session.execute(select(Chat).where(Chat.user_id == user.id))).scalars()}
    assert {"support", "notes"} <= chats
    # токен регистрации одноразовый
    with pytest.raises(NotFoundError):
        await auth.register_complete("reg-token", "123456", None, None)


async def test_wrong_code_counts_attempts_and_locks_after_the_limit(auth, db_session):
    user = await _user(db_session)
    (await _pending(db_session)).user_id = user.id
    code = await _code(db_session, PHONE, PURPOSE_REGISTRATION)

    for _ in range(5):
        with pytest.raises(InvalidCodeError, match="Неверный код"):
            await auth.register_complete("reg-token", "000000", None, None)
    assert code.attempts == 5

    # лимит исчерпан — даже верный код больше не принимается
    with pytest.raises(InvalidCodeError, match="Превышено"):
        await auth.register_complete("reg-token", "123456", None, None)
    assert user.is_phone_verified is False


@pytest.mark.parametrize("problem", ["expired", "used", "other_purpose", "missing"])
async def test_unusable_code_is_rejected(auth, db_session, problem):
    user = await _user(db_session)
    (await _pending(db_session)).user_id = user.id
    if problem == "expired":
        await _code(db_session, PHONE, PURPOSE_REGISTRATION, expires_in=timedelta(minutes=-1))
    elif problem == "used":
        (await _code(db_session, PHONE, PURPOSE_REGISTRATION)).used_at = datetime.now(timezone.utc)
    elif problem == "other_purpose":
        await _code(db_session, PHONE, PURPOSE_PASSWORD_RESET)  # код сброса пароля регистрацию не подтверждает

    with pytest.raises(InvalidCodeError):
        await auth.register_complete("reg-token", "123456", None, None)

    assert user.is_phone_verified is False


async def test_register_complete_before_the_number_is_confirmed(auth, db_session):
    await _pending(db_session)

    with pytest.raises(InvalidCodeError, match="Номер ещё не подтверждён"):
        await auth.register_complete("reg-token", "123456", None, None)


async def test_register_complete_for_an_already_confirmed_user(auth, db_session):
    user = await _user(db_session, verified=True)
    (await _pending(db_session)).user_id = user.id
    await _code(db_session, PHONE, PURPOSE_REGISTRATION)

    with pytest.raises(AlreadyExistsError):
        await auth.register_complete("reg-token", "123456", None, None)


# --- регистрация: повторная отправка ---

async def test_resend_before_contact_returns_the_deep_link(auth, db_session):
    await _pending(db_session)

    link, cooldown = await auth.register_resend_code("reg-token")

    assert "reg-token" in link and cooldown > 0


async def test_resend_sends_a_new_code_and_respects_cooldown(auth, db_session, telegram):
    user = await _user(db_session)
    (await _pending(db_session)).user_id = user.id
    await _link(db_session, user)

    assert await auth.register_resend_code("reg-token") == (None, 60)
    assert len(telegram.codes) == 1 and telegram.codes[0][0] == "9001"

    with pytest.raises(RateLimitError):
        await auth.register_resend_code("reg-token")
    assert len(telegram.codes) == 1


async def test_resend_without_telegram_link_or_for_confirmed_user(auth, db_session, telegram):
    user = await _user(db_session)
    (await _pending(db_session)).user_id = user.id

    with pytest.raises(NotFoundError, match="Telegram не подключён"):
        await auth.register_resend_code("reg-token")

    user.is_phone_verified = True
    with pytest.raises(AlreadyExistsError):
        await auth.register_resend_code("reg-token")


# --- сброс пароля ---

@pytest.mark.parametrize("case", ["unknown", "inactive", "unverified", "no_link"])
async def test_password_reset_start_sends_nothing_and_looks_the_same(auth, db_session, telegram, case):
    if case != "unknown":
        user = await _user(db_session, verified=case != "unverified", active=case != "inactive")
        if case != "no_link":
            # у неактивного и неподтверждённого связка с Telegram есть, но код всё равно не уходит
            await _link(db_session, user)

    result = await auth.password_reset_start(PHONE, "telegram")

    assert result == (60, None)  # один и тот же ответ: нельзя отличить «нет такого» от «есть»
    assert telegram.codes == []
    assert await _codes(db_session, PHONE, PURPOSE_PASSWORD_RESET) == []


async def test_password_reset_start_delivers_a_code_once_per_cooldown(auth, db_session, telegram):
    user = await _user(db_session, verified=True)
    await _link(db_session, user)

    assert await auth.password_reset_start(PHONE, "telegram") == (60, None)
    assert await auth.password_reset_start(PHONE, "telegram") == (60, None)

    assert len(telegram.codes) == 1
    assert len(await _codes(db_session, PHONE, PURPOSE_PASSWORD_RESET)) == 1


async def test_password_reset_with_the_right_code(auth, db_session):
    user = await _user(db_session, verified=True)
    await auth._issue_tokens(user, None, None)
    await auth._issue_tokens(user, None, None)
    await _code(db_session, PHONE, PURPOSE_PASSWORD_RESET)

    await auth.password_reset_complete(PHONE, "123456", "newPassword8", "newPassword8")

    assert verify_password("newPassword8", user.hashed_password)
    assert await _active_sessions(db_session, user) == []  # все сессии закрыты
    with pytest.raises(InvalidCodeError):  # код одноразовый
        await auth.password_reset_complete(PHONE, "123456", "another8Pass", "another8Pass")
    assert verify_password("newPassword8", user.hashed_password)


async def test_password_reset_rejections(auth, db_session):
    user = await _user(db_session, verified=True)
    code = await _code(db_session, PHONE, PURPOSE_PASSWORD_RESET)

    with pytest.raises(InvalidCodeError, match="не совпадают"):
        await auth.password_reset_complete(PHONE, "123456", "newPassword8", "different8")

    with pytest.raises(InvalidCodeError, match="Неверный код"):
        await auth.password_reset_complete(PHONE, "000000", "newPassword8", "newPassword8")
    assert code.attempts == 1

    code.attempts = 5
    with pytest.raises(InvalidCodeError, match="Превышено"):
        await auth.password_reset_complete(PHONE, "123456", "newPassword8", "newPassword8")
    assert verify_password("password8", user.hashed_password)


@pytest.mark.parametrize("case", ["unknown_phone", "inactive", "unverified", "registration_code"])
async def test_password_reset_cannot_be_forced(auth, db_session, case):
    if case == "unknown_phone":
        await _code(db_session, PHONE, PURPOSE_PASSWORD_RESET)
    else:
        await _user(db_session, verified=case != "unverified", active=case != "inactive")
        await _code(db_session, PHONE, PURPOSE_REGISTRATION if case == "registration_code" else PURPOSE_PASSWORD_RESET)

    with pytest.raises(InvalidCodeError, match="Код не найден или истёк"):
        await auth.password_reset_complete(PHONE, "123456", "newPassword8", "newPassword8")


# --- смена пароля ---

async def test_change_password_closes_all_sessions(auth, db_session):
    user = await _user(db_session, verified=True)
    await auth._issue_tokens(user, None, None)

    await auth.change_password(user, "password8", "newPassword8", "newPassword8")

    assert verify_password("newPassword8", user.hashed_password)
    assert await _active_sessions(db_session, user) == []


@pytest.mark.parametrize("old,new,confirm,error", [
    ("wrong-old-pass", "newPassword8", "newPassword8", AuthenticationError),
    ("password8", "password8", "password8", InvalidCodeError),
    ("password8", "newPassword8", "other-Pass88", InvalidCodeError),
])
async def test_change_password_rejections_keep_the_old_password(auth, db_session, old, new, confirm, error):
    user = await _user(db_session, verified=True)
    await auth._issue_tokens(user, None, None)

    with pytest.raises(error):
        await auth.change_password(user, old, new, confirm)

    assert verify_password("password8", user.hashed_password)
    assert len(await _active_sessions(db_session, user)) == 1


# --- профиль ---

async def test_update_profile_changes_only_given_fields(auth, db_session):
    user = await _user(db_session, verified=True)
    user.organization_name = "ООО Старое"
    user.email = "old@example.by"

    await auth.update_profile(user, full_name="Петров Пётр", email=None, organization_name=None, contact_phone="+375291110000")

    assert user.full_name == "Петров Пётр"
    assert user.contact_phone == "+375291110000"
    assert user.organization_name == "ООО Старое" and user.email == "old@example.by"


async def test_delete_email_clears_it(auth, db_session):
    user = await _user(db_session, verified=True)
    user.email = "old@example.by"

    await auth.delete_email(user)

    assert user.email is None


# --- удаление аккаунта ---

async def _own_stuff(db_session, user):
    """Всё личное, что должно исчезнуть вместе с аккаунтом."""
    from app.models.device_token import DeviceToken
    from app.models.message import Message
    from app.models.notification import Notification
    from app.models.notification_settings import NotificationSettings
    from app.models.password_reset_request import PasswordResetRequest
    from app.models.user_favorite import UserFavorite
    from app.repositories.chat import MessageRepository

    await auth_for(db_session)._issue_tokens(user, None, None)
    await auth_for(db_session)._issue_tokens(user, None, None)
    chat = Chat(user_id=user.id, chat_type="support")
    db_session.add(chat)
    await db_session.flush()
    message = Message(chat_id=chat.id, sender_id=user.id, text="Личная переписка")
    db_session.add(message)
    await db_session.flush()
    await MessageRepository(db_session).add_attachment(message.id, {
        "attachment_type": "image", "file_url": "/static/files/private.jpg", "file_name": "private.jpg",
        "file_size_bytes": 10, "mime_type": "image/jpeg",
    })
    db_session.add_all([
        DeviceToken(user_id=user.id, token="fcm-token", platform="android"),
        Notification(user_id=user.id, type="chat_message", title="Привет", body="текст"),
        NotificationSettings(user_id=user.id),
        UserFavorite(user_id=user.id, entity_type="document", entity_id=1),
        PasswordResetRequest(user_id=user.id, hashed_password="hash-of-new-password"),
    ])
    await _link(db_session, user)
    await _code(db_session, user.phone, PURPOSE_PASSWORD_RESET)
    return chat, message


def auth_for(db_session):
    return AuthService(db_session)


async def _count(db_session, model, **where):
    from sqlalchemy import func
    stmt = select(func.count()).select_from(model)
    for column, value in where.items():
        stmt = stmt.where(getattr(model, column) == value)
    return (await db_session.execute(stmt)).scalar()


async def test_deleted_account_is_anonymized_and_personal_data_is_erased(auth, db_session, monkeypatch):
    from app.models.device_token import DeviceToken
    from app.models.message import Message
    from app.models.notification import Notification
    from app.models.password_reset_request import PasswordResetRequest
    from app.models.user_favorite import UserFavorite
    from app.services import chat_service

    cleaned = []
    monkeypatch.setattr(chat_service, "_schedule_attachment_cleanup", cleaned.append)
    user = await _user(db_session, verified=True)
    user.email = "ivan@example.by"
    user.contact_phone = "+375291110000"
    user.organization_name = "ООО Ромашка"
    user.user_type = "organization"
    chat, message = await _own_stuff(db_session, user)
    user_id = user.id

    await auth.delete_account(user)

    kept = await db_session.get(User, user_id)
    assert kept is not None  # запись остаётся — на неё ссылаются заявки
    assert kept.is_active is False and kept.is_phone_verified is False and kept.is_verified is False
    assert (kept.phone, kept.contact_phone, kept.email, kept.organization_name, kept.user_type) == (None,) * 5
    assert kept.login == f"_deleted_{user_id}" and kept.full_name == "Удалённый пользователь"
    assert await _active_sessions(db_session, kept) == []
    # личное стёрто
    assert await _count(db_session, Chat, user_id=user_id) == 0
    assert await _count(db_session, Message, chat_id=chat.id) == 0
    for model in (DeviceToken, Notification, UserFavorite, PasswordResetRequest, MessengerLink):
        assert await _count(db_session, model, user_id=user_id) == 0, model.__name__
    assert await _codes(db_session, PHONE, PURPOSE_PASSWORD_RESET) == []
    # файлы переписки уйдут фоновой зачисткой
    assert cleaned == [["/static/files/private.jpg"]]


async def test_service_history_survives_without_a_person(auth, db_session, make_project, make_reclamation):
    from app.models.service_request import ServiceRequest
    from app.repositories.service_request import ServiceRequestRepository

    user = await _user(db_session, verified=True)
    project = await make_project()
    request = ServiceRequest(
        user_id=user.id, project_id=project.id, request_type="repair",
        is_under_warranty=True, description="Течёт насос",
    )
    db_session.add(request)
    reclamation = await make_reclamation(user=user)
    await db_session.flush()
    user_id = user.id

    await auth.delete_account(user)

    assert (await db_session.get(ServiceRequest, request.id)).user_id == user_id
    await db_session.refresh(reclamation)
    assert reclamation.user_id == user_id and reclamation.description
    rows, _ = await ServiceRequestRepository(db_session).list_admin(search="насос")
    assert [r[1].full_name for r in rows if r[0].id == request.id] == ["Удалённый пользователь"]


async def test_deleted_account_cannot_sign_in_and_the_number_is_free_again(auth, db_session, make_user):
    user = await _user(db_session, verified=True)
    user_id = user.id

    await auth.delete_account(user)

    with pytest.raises(AuthenticationError):
        await auth.login(PHONE, "password8", None, None)
    # вход по логину-заглушке не должен падать ошибкой хеша, только отказывать
    with pytest.raises(AuthenticationError):
        await auth.admin_login(f"_deleted_{user_id}", "anything-8-chars", None, None)
    again = await make_user(phone=PHONE)
    assert again.id != user_id


async def test_deleted_users_are_hidden_from_the_admin_list(auth, db_session):
    from app.repositories.user import UserRepository
    user = await _user(db_session, verified=True)
    user_id = user.id
    await auth.delete_account(user)

    items, _ = await UserRepository(db_session).admin_search(role="user")

    assert user_id not in [u.id for u, _ in items]


@pytest.mark.parametrize("role", ["operator", "admin", "superadmin"])
async def test_staff_cannot_delete_their_own_account(auth, db_session, make_user, role):
    from app.core.exceptions import PermissionDeniedError
    staff = await make_user(role, phone=None, login=f"{role}-1")

    with pytest.raises(PermissionDeniedError):
        await auth.delete_account(staff)

    assert staff.is_active is True and staff.login == f"{role}-1"


async def test_password_check_does_not_crash_on_a_stored_non_hash(auth, db_session, make_user):
    """В базе у ранее удалённых сотрудников стоит строка-заглушка, а не хеш."""
    legacy = await make_user("operator", phone=None, login="_deleted_900", hashed_password="DELETED")

    with pytest.raises(AuthenticationError):
        await auth.admin_login("_deleted_900", "anything-8-chars", None, None)
    assert verify_password("x", legacy.hashed_password) is False


async def test_deleted_operator_cannot_sign_in_either(auth, db_session, make_user):
    """Удаление оператора администратором использует ту же заглушку пароля —
    попытка войти под `_deleted_N` не должна падать ошибкой разбора хеша."""
    from app.services.admin_user_service import AdminUserService
    admin = await make_user("admin")
    operator = await make_user("operator", phone=None, login="op-1")
    operator_id = operator.id

    await AdminUserService(db_session).delete_operator(operator_id, admin.id, "admin")

    with pytest.raises(AuthenticationError):
        await auth.admin_login(f"_deleted_{operator_id}", "anything-8-chars", None, None)
