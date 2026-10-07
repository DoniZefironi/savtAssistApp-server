"""Синхронизация сотрудников Bitrix -> операторы/админы/суперадмины.
Bitrix не вызывается: список пользователей портала подаётся готовым словарём.
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from app.core.dependencies import get_current_user
from app.core.exceptions import AuthenticationError
from app.core.security import verify_password
from app.models.refresh_token import RefreshToken
from app.models.role import Role
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.bitrix_staff_sync import sync_staff
from app.utils.phone import normalize_loose_phone

PASSWORD = "Initial-pass-1"


def bx(user_id, departments, phone="+375 29 111-22-33", **kw):
    data = {
        "ID": str(user_id), "ACTIVE": True, "USER_TYPE": "employee",
        "LAST_NAME": "Иванов", "NAME": f"Иван{user_id}", "SECOND_NAME": None,
        "UF_DEPARTMENT": departments, "WORK_PHONE": phone, "PERSONAL_MOBILE": None,
        "EMAIL": f"user{user_id}@example.by",
    }
    data.update(kw)
    return data


async def _by_bitrix_id(db_session, bitrix_id):
    return (await db_session.execute(select(User).where(User.bitrix_user_id == bitrix_id))).scalar_one_or_none()


async def _role_name(db_session, user):
    return (await db_session.get(Role, user.role_id)).name


# --- нормализация телефона ---

@pytest.mark.parametrize("raw", [
    "+375 (29) 111-22-33", "375291112233", "80291112233", "8 029 111-22-33", "291112233", "+375291112233",
])
def test_phone_formats_normalize_to_e164(raw):
    assert normalize_loose_phone(raw) == "+375291112233"


def test_phone_missing_and_garbage():
    assert normalize_loose_phone(None) is None
    assert normalize_loose_phone("  ") is None
    assert normalize_loose_phone("12") is False


# --- создание ---

async def test_creates_staff_with_phone_login_and_forced_password_change(db_session):
    report = await sync_staff(db_session, [bx(7, [45])], PASSWORD)

    user = await _by_bitrix_id(db_session, 7)
    assert [r["bitrix_user_id"] for r in report.created] == [7]
    assert user.login == "+375291112233"
    assert user.phone is None
    assert user.full_name == "Иванов Иван7"
    assert await _role_name(db_session, user) == "operator"
    assert user.must_change_password is True
    assert user.is_active and user.is_verified
    assert verify_password(PASSWORD, user.hashed_password)


async def test_highest_role_wins_across_departments(db_session):
    await sync_staff(db_session, [bx(1, [45, 73], phone="+375291110001"), bx(2, [91, 25], phone="+375291110002")], PASSWORD)

    assert await _role_name(db_session, await _by_bitrix_id(db_session, 1)) == "superadmin"
    assert await _role_name(db_session, await _by_bitrix_id(db_session, 2)) == "admin"


async def test_roles_follow_the_agreed_departments(db_session):
    cases = {
        1: ("superadmin", [1]), 2: ("operator", [69]), 3: ("superadmin", [73]), 4: ("operator", [27]),
        5: ("operator", [23]), 6: ("admin", [25]), 7: ("superadmin", [95]), 8: ("operator", [45]),
        9: ("operator", [57]), 10: ("operator", [63]), 11: ("admin", [3]), 12: ("operator", [91]),
        13: ("operator", [101]), 14: ("operator", [97]), 15: ("operator", [71]), 16: ("admin", [65]),
    }
    users = [bx(i, deps, phone=f"+37529111{i:04d}") for i, (_, deps) in cases.items()]

    await sync_staff(db_session, users, PASSWORD)

    for i, (expected, _) in cases.items():
        assert await _role_name(db_session, await _by_bitrix_id(db_session, i)) == expected, i


async def test_skips_maternity_department_inactive_and_extranet(db_session):
    users = [
        bx(1, [99], phone="+375291110001"),
        bx(2, [45], phone="+375291110002", ACTIVE=False),
        bx(3, [45], phone="+375291110003", USER_TYPE="extranet"),
        bx(4, [], phone="+375291110004"),
    ]

    report = await sync_staff(db_session, users, PASSWORD)

    assert report.created == []
    for i in (1, 2, 3, 4):
        assert await _by_bitrix_id(db_session, i) is None


async def test_reports_missing_invalid_and_duplicate_phones(db_session):
    users = [
        bx(1, [45], phone=None),
        bx(2, [45], phone="12"),
        bx(3, [45], phone="+375291119999"),
        bx(4, [91], phone="80291119999"),
        bx(5, [45], phone="+375291110005"),
    ]

    report = await sync_staff(db_session, users, PASSWORD)

    assert [r["bitrix_user_id"] for r in report.skipped_no_phone] == [1]
    assert [r["bitrix_user_id"] for r in report.skipped_invalid_phone] == [2]
    assert sorted(r["bitrix_user_id"] for r in report.skipped_duplicate_phone) == [3, 4]
    assert [r["bitrix_user_id"] for r in report.created] == [5]
    assert await _by_bitrix_id(db_session, 3) is None and await _by_bitrix_id(db_session, 4) is None


async def test_without_initial_password_nobody_is_created(db_session):
    report = await sync_staff(db_session, [bx(1, [45])], "")

    assert report.created == []
    assert [r["bitrix_user_id"] for r in report.skipped_no_password] == [1]


async def test_second_run_changes_nothing(db_session):
    users = [bx(1, [45]), bx(2, [25], phone="+375291110002")]
    await sync_staff(db_session, users, PASSWORD)

    report = await sync_staff(db_session, users, PASSWORD)

    assert report.counts() == {name: 0 for name in report.counts()}


async def test_email_already_taken_is_left_empty(db_session, make_user):
    await make_user(email="user1@example.by")

    await sync_staff(db_session, [bx(1, [45])], PASSWORD)

    assert (await _by_bitrix_id(db_session, 1)).email is None


# --- уже существующие учётки ---

async def test_phone_of_mobile_app_user_is_a_conflict_and_account_is_untouched(db_session, make_user):
    customer = await make_user(phone="+375291112233", full_name="Клиент Клиентов")

    report = await sync_staff(db_session, [bx(1, [73])], PASSWORD)

    assert [r["bitrix_user_id"] for r in report.skipped_conflict] == [1]
    assert await _by_bitrix_id(db_session, 1) is None
    await db_session.refresh(customer)
    assert customer.bitrix_user_id is None
    assert await _role_name(db_session, customer) == "user"


async def test_existing_staff_is_linked_by_phone_and_role_is_only_raised(db_session, make_user):
    operator = await make_user("operator", phone=None, login="+375291112233")
    superadmin = await make_user("superadmin", phone=None, login="+375291110002")

    report = await sync_staff(
        db_session,
        [bx(1, [73]), bx(2, [45], phone="+375291110002")],
        PASSWORD,
    )

    assert sorted(r["bitrix_user_id"] for r in report.linked) == [1, 2]
    await db_session.refresh(operator)
    await db_session.refresh(superadmin)
    assert operator.bitrix_user_id == 1
    assert await _role_name(db_session, operator) == "superadmin"
    assert superadmin.bitrix_user_id == 2
    # отдел даёт оператора, но вручную заведённый суперадмин не понижается
    assert await _role_name(db_session, superadmin) == "superadmin"
    assert not operator.must_change_password


# --- обновление и деактивация ---

async def test_role_follows_department_changes_for_synced_staff(db_session):
    await sync_staff(db_session, [bx(1, [45])], PASSWORD)

    report = await sync_staff(db_session, [bx(1, [25])], PASSWORD)

    assert [r["from"] for r in report.role_changed] == ["operator"]
    assert await _role_name(db_session, await _by_bitrix_id(db_session, 1)) == "admin"


async def test_fired_employee_is_deactivated_and_sessions_revoked(db_session):
    await sync_staff(db_session, [bx(1, [45])], PASSWORD)
    user = await _by_bitrix_id(db_session, 1)
    db_session.add(RefreshToken(user_id=user.id, token_hash="h", expires_at=_far_future()))
    await db_session.flush()

    report = await sync_staff(db_session, [bx(1, [45], ACTIVE=False)], PASSWORD)

    assert [r["bitrix_user_id"] for r in report.deactivated] == [1]
    await db_session.refresh(user)
    assert user.is_active is False
    tokens = (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))).scalars().all()
    assert tokens and all(t.revoked_at is not None for t in tokens)


async def test_employee_moved_to_department_without_role_or_removed_is_deactivated(db_session):
    await sync_staff(db_session, [bx(1, [45]), bx(2, [45], phone="+375291110002")], PASSWORD)

    report = await sync_staff(db_session, [bx(1, [99])], PASSWORD)

    assert sorted(r["bitrix_user_id"] for r in report.deactivated) == [1, 2]
    assert (await _by_bitrix_id(db_session, 1)).is_active is False
    assert (await _by_bitrix_id(db_session, 2)).is_active is False


async def test_returning_employee_is_reactivated(db_session):
    await sync_staff(db_session, [bx(1, [45])], PASSWORD)
    await sync_staff(db_session, [bx(1, [45], ACTIVE=False)], PASSWORD)

    report = await sync_staff(db_session, [bx(1, [45])], PASSWORD)

    assert [r["bitrix_user_id"] for r in report.reactivated] == [1]
    assert (await _by_bitrix_id(db_session, 1)).is_active is True


async def test_accounts_not_linked_to_bitrix_are_never_deactivated(db_session, make_user):
    manual_admin = await make_user("admin", phone=None, login="boss")
    customer = await make_user()

    await sync_staff(db_session, [], PASSWORD)

    await db_session.refresh(manual_admin)
    await db_session.refresh(customer)
    assert manual_admin.is_active and customer.is_active


async def test_changed_phone_in_bitrix_changes_login(db_session):
    await sync_staff(db_session, [bx(1, [45])], PASSWORD)

    await sync_staff(db_session, [bx(1, [45], phone="+375299998877")], PASSWORD)

    assert (await _by_bitrix_id(db_session, 1)).login == "+375299998877"


# --- вход и обязательная смена пароля ---

async def test_staff_logs_in_with_phone_typed_any_way_and_must_change_password(db_session):
    await sync_staff(db_session, [bx(1, [45])], PASSWORD)
    service = AuthService(db_session)

    for typed in ("+375291112233", "+375 (29) 111-22-33", "375291112233", "80291112233", "291112233"):
        _, _, must_change = await service.admin_login(typed, PASSWORD, None, None)
        assert must_change is True, typed

    with pytest.raises(AuthenticationError):
        await service.admin_login("+375291112233", "wrong-password", None, None)


async def test_login_of_deactivated_employee_is_refused(db_session):
    await sync_staff(db_session, [bx(1, [45])], PASSWORD)
    await sync_staff(db_session, [bx(1, [45], ACTIVE=False)], PASSWORD)

    with pytest.raises(AuthenticationError):
        await AuthService(db_session).admin_login("+375291112233", PASSWORD, None, None)


async def test_password_change_clears_the_flag(db_session):
    await sync_staff(db_session, [bx(1, [45])], PASSWORD)
    user = await _by_bitrix_id(db_session, 1)

    await AuthService(db_session).change_password(user, PASSWORD, "New-pass-12345", "New-pass-12345")

    assert user.must_change_password is False
    _, _, must_change = await AuthService(db_session).admin_login("+375291112233", "New-pass-12345", None, None)
    assert must_change is False


async def _call_current_user(db_session, user, method, path):
    from fastapi.security import HTTPAuthorizationCredentials
    from app.core.security import create_access_token

    role = await _role_name(db_session, user)
    token = create_access_token(user_id=user.id, role=role)
    request = Request({"type": "http", "method": method, "path": path, "headers": []})
    return await get_current_user(
        request, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db_session,
    )


async def test_api_is_closed_until_password_is_changed(db_session):
    await sync_staff(db_session, [bx(1, [45])], PASSWORD)
    user = await _by_bitrix_id(db_session, 1)

    for method, path in (("POST", "/auth/password-change"), ("POST", "/auth/logout"), ("GET", "/auth/me")):
        assert (await _call_current_user(db_session, user, method, path)).id == user.id

    for method, path in (("GET", "/admin/users"), ("GET", "/chats"), ("PATCH", "/auth/me")):
        with pytest.raises(HTTPException) as exc:
            await _call_current_user(db_session, user, method, path)
        assert exc.value.status_code == 403

    user.must_change_password = False
    assert (await _call_current_user(db_session, user, "GET", "/admin/users")).id == user.id


def _far_future():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(days=30)
