"""PhoneChangeService — единственный способ сменить номер (логин) в системе,
только через заявку с решением администратора: самообслуживания нет
намеренно, т.к. SMS отключены и подтвердить владение новым номером
автоматически нечем (см. докстринг сервиса). Ничего внешнего не трогает:
NotificationService/send_push сами безопасно no-op без Firebase/токенов
устройства — ни один из этих тестов ничего никуда не отправляет.
"""
import pytest

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.schemas.requests import RejectRequestIn
from app.services.phone_change_service import PhoneChangeService


@pytest.fixture
def svc(db_session):
    return PhoneChangeService(db_session)


# --- create_request ---

async def test_create_request_for_own_current_number_rejected(svc, make_user):
    user = await make_user()
    with pytest.raises(AlreadyExistsError, match="уже ваш текущий номер"):
        await svc.create_request(user, user.phone, None)


async def test_create_request_for_number_taken_by_another_user_rejected(svc, make_user):
    user = await make_user()
    other = await make_user()
    with pytest.raises(AlreadyExistsError, match="уже занят"):
        await svc.create_request(user, other.phone, None)


async def test_create_request_while_already_pending_rejected(svc, make_user):
    user = await make_user()
    await svc.create_request(user, "+375291110000", "первая заявка")
    with pytest.raises(AlreadyExistsError, match="уже есть необработанная заявка"):
        await svc.create_request(user, "+375291110001", "вторая заявка")


async def test_create_request_success_snapshots_old_phone(svc, make_user):
    user = await make_user()
    req = await svc.create_request(user, "+375291110000", "сменил оператора")
    assert req.status == "pending"
    assert req.old_phone == user.phone
    assert req.new_phone == "+375291110000"


# --- cancel_my_request ---

async def test_cancel_without_pending_request_raises(svc, make_user):
    user = await make_user()
    with pytest.raises(NotFoundError):
        await svc.cancel_my_request(user.id)


async def test_cancel_marks_cancelled(svc, make_user):
    user = await make_user()
    await svc.create_request(user, "+375291110000", None)
    await svc.cancel_my_request(user.id)

    assert await svc.get_my_request(user.id) is None  # больше не pending, значит не виден


# --- approve ---

async def test_approve_unknown_request_raises(svc, make_user):
    admin = await make_user(role_name="admin")
    with pytest.raises(NotFoundError):
        await svc.approve(999999, None, admin.id, "admin")


async def test_approve_already_resolved_request_raises(svc, make_user):
    user = await make_user()
    admin = await make_user(role_name="admin")
    req = await svc.create_request(user, "+375291110000", None)
    await svc.approve(req.id, None, admin.id, "admin")

    with pytest.raises(AlreadyExistsError, match="уже обработана"):
        await svc.approve(req.id, None, admin.id, "admin")


async def test_approve_changes_user_phone_and_marks_approved(svc, make_user, db_session):
    user = await make_user()
    admin = await make_user(role_name="admin")
    req = await svc.create_request(user, "+375291110000", None)

    await svc.approve(req.id, "подтвердил по телефону", admin.id, "admin")

    await db_session.refresh(user)
    assert user.phone == "+375291110000"

    from app.repositories.phone_change import PhoneChangeRequestRepository
    stored = await PhoneChangeRequestRepository(db_session).get_by_id(req.id)
    assert stored.status == "approved"
    assert stored.resolved_by_admin_id == admin.id
    assert stored.admin_response == "подтвердил по телефону"


async def test_approve_rejects_if_number_taken_after_submission(svc, make_user, db_session):
    # гонка: пока заявка ждала, кто-то другой успел занять этот номер сам
    user = await make_user()
    admin = await make_user(role_name="admin")
    req = await svc.create_request(user, "+375291110000", None)

    await make_user(phone="+375291110000")  # номер заняли, пока заявка висела

    with pytest.raises(AlreadyExistsError, match="уже занят"):
        await svc.approve(req.id, None, admin.id, "admin")


# --- reject ---

async def test_reject_unknown_request_raises(svc, make_user):
    admin = await make_user(role_name="admin")
    with pytest.raises(NotFoundError):
        await svc.reject(999999, RejectRequestIn(admin_response="причина"), admin.id, "admin")


async def test_reject_marks_rejected_and_does_not_change_phone(svc, make_user, db_session):
    user = await make_user()
    admin = await make_user(role_name="admin")
    original_phone = user.phone
    req = await svc.create_request(user, "+375291110000", None)

    await svc.reject(req.id, RejectRequestIn(admin_response="не подтвердили владение"), admin.id, "admin")

    await db_session.refresh(user)
    assert user.phone == original_phone  # номер НЕ изменился

    from app.repositories.phone_change import PhoneChangeRequestRepository
    stored = await PhoneChangeRequestRepository(db_session).get_by_id(req.id)
    assert stored.status == "rejected"
    assert stored.admin_response == "не подтвердили владение"


# --- pending_rivals: сколько ещё аккаунтов претендуют на тот же номер ---

async def test_pending_rivals_counts_other_requests_for_same_number(svc, make_user):
    user_a = await make_user()
    user_b = await make_user()
    await svc.create_request(user_a, "+375299990000", None)
    await svc.create_request(user_b, "+375299990000", None)  # тот же номер, другой заявитель

    page = await svc.list_requests()
    rivals_by_user = {item.user_id: item.pending_rivals for item in page.items}
    assert rivals_by_user[user_a.id] == 2
    assert rivals_by_user[user_b.id] == 2


async def test_pending_rivals_is_one_when_no_competing_claims(svc, make_user):
    user = await make_user()
    await svc.create_request(user, "+375299990001", None)

    page = await svc.list_requests()
    item = next(i for i in page.items if i.user_id == user.id)
    assert item.pending_rivals == 1
