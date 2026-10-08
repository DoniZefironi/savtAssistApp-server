"""Администрирование пользователей: создание учёток (админ, оператор, клиент),
списки и карточка, блокировка/подтверждение, удаление операторов и админов,
защита служебных и старших учёток, пользователи шкафа."""
import pytest
from sqlalchemy import select

from app.core.constants import BITRIX_USER_LOGIN, BOT_USER_LOGIN
from app.core.exceptions import AlreadyExistsError, AuthenticationError, NotFoundError, PermissionDeniedError
from app.core.security import verify_password
from app.models.audit_log import AuditLog
from app.models.chat import Chat
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.admin_users import CreateAdminIn, CreateOperatorIn, CreateUserIn
from app.services.admin_user_service import AdminUserService
from app.services.auth_service import AuthService


@pytest.fixture
def svc(db_session):
    return AdminUserService(db_session)


async def _audit(db_session, action):
    return list((await db_session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _system_user(make_user, db_session, login=BITRIX_USER_LOGIN, role="operator"):
    existing = (await db_session.execute(select(User).where(User.login == login))).scalar_one_or_none()
    return existing or await make_user(role, phone=None, login=login, full_name="Ася")


# --- создание ---

async def test_create_operator(svc, db_session, make_user):
    admin = await make_user("admin")

    out = await svc.create_operator(CreateOperatorIn(login="Operator7", password="password8", full_name="Иванов Иван"), admin.id, "admin")

    user = await db_session.get(User, out.id)
    assert out.role == "operator" and out.login == "operator7"  # логин приводится к нижнему регистру
    assert user.is_active and user.is_verified and user.is_phone_verified and user.phone is None
    assert verify_password("password8", user.hashed_password)
    [entry] = await _audit(db_session, "user.create_operator")
    assert (entry.actor_id, entry.actor_role, entry.entity_id) == (admin.id, "admin", user.id)
    # новый оператор может войти по логину
    await AuthService(db_session).admin_login("operator7", "password8", None, None)


async def test_create_admin(svc, db_session, make_user):
    superadmin = await make_user("superadmin")

    out = await svc.create_admin(CreateAdminIn(login="boss", password="password8"), superadmin.id, "superadmin")

    assert out.role == "admin" and out.full_name is None
    assert len(await _audit(db_session, "user.create_admin")) == 1


@pytest.mark.parametrize("method,schema", [("create_operator", CreateOperatorIn), ("create_admin", CreateAdminIn)])
async def test_staff_login_must_be_unique(svc, make_user, method, schema):
    actor = await make_user("superadmin")
    await make_user("operator", phone=None, login="taken")

    with pytest.raises(AlreadyExistsError):
        await getattr(svc, method)(schema(login="taken", password="password8"), actor.id, "superadmin")


def test_login_with_spaces_is_rejected_by_the_schema():
    with pytest.raises(ValueError):
        CreateOperatorIn(login="two words", password="password8")


async def test_create_user_directly(svc, db_session, make_user):
    admin = await make_user("admin")
    data = CreateUserIn(
        phone="+375291234567", password="password8", full_name="Сидоров Семён",
        user_type="organization", organization_name="ООО Ромашка", contact_phone="+375291110000",
    )

    out = await svc.create_user(data, admin.id, "admin")

    user = await db_session.get(User, out.id)
    assert out.role == "user" and user.phone == "+375291234567" and user.contact_phone == "+375291110000"
    assert user.is_phone_verified and user.is_verified  # без подтверждения через Telegram человек не смог бы войти
    chats = {c.chat_type for c in (await db_session.execute(select(Chat).where(Chat.user_id == user.id))).scalars()}
    assert {"support", "notes"} <= chats
    [entry] = await _audit(db_session, "user.create")
    assert entry.payload == {"phone": "+375291234567"}
    await AuthService(db_session).login("+375291234567", "password8", None, None)


async def test_create_user_with_a_taken_phone(svc, make_user):
    admin = await make_user("admin")
    await make_user(phone="+375291234567")

    with pytest.raises(AlreadyExistsError):
        await svc.create_user(
            CreateUserIn(phone="+375291234567", password="password8", full_name="Дубль", user_type="individual"),
            admin.id, "admin",
        )


def test_organization_needs_a_name():
    with pytest.raises(ValueError):
        CreateUserIn(phone="+375291234567", password="password8", full_name="Х", user_type="organization")


# --- списки и карточка ---

async def test_lists_split_by_role_and_hide_senior_and_system_accounts(svc, db_session, make_user):
    customer = await make_user(full_name="Клиент Клиентов")
    operator = await make_user("operator", full_name="Оператор Олег")
    admin = await make_user("admin", full_name="Админ Анна")
    await make_user("superadmin", full_name="Суперадмин")
    await _system_user(make_user, db_session)
    await _system_user(make_user, db_session, login=BOT_USER_LOGIN)

    users = await svc.list_users(role="user", size=100)
    operators = await svc.list_users(role="operator", size=100)
    admins = await svc.list_users(role="admin", size=100)

    assert customer.id in [u.id for u in users.items] and operator.id not in [u.id for u in users.items]
    assert [u.role for u in operators.items] == ["operator"] * len(operators.items)
    assert operator.id in [u.id for u in operators.items]
    assert "Ася" not in [u.full_name for u in operators.items]  # служебная учётка Bitrix не показывается
    assert admin.id in [u.id for u in admins.items]


async def test_list_filters_and_search(svc, make_user):
    active = await make_user(full_name="Иванов Иван", is_verified=True)
    banned = await make_user(full_name="Иванов Пётр", is_active=False)
    await make_user(full_name="Петров Пётр")

    by_search = await svc.list_users(query="Иванов", role="user")
    inactive = await svc.list_users(is_active=False, role="user")

    assert {u.id for u in by_search.items} == {active.id, banned.id}
    assert [u.id for u in inactive.items] == [banned.id]


async def test_user_detail_shows_projects_and_directly_added_cabinets(
    svc, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet,
):
    user = await make_user(full_name="Клиент Клиентов", email="k@example.by")
    project = await make_project(name="Космос")
    await link_user_project(user, project)
    cabinet = await make_cabinet(admin_internal_name="Личный ШУ")
    await link_user_cabinet(user, cabinet)

    detail = await svc.get_user_detail(user.id)

    assert detail.email == "k@example.by" and detail.role == "user"
    assert [p.name for p in detail.projects] == ["Космос"]
    assert [c.cabinet_id for c in detail.cabinets] == [cabinet.id]


async def test_detail_of_senior_accounts_is_hidden_unless_asked_for(svc, make_user):
    admin = await make_user("admin")

    with pytest.raises(NotFoundError):
        await svc.get_user_detail(admin.id)
    assert (await svc.get_user_detail(admin.id, allowed_roles=("admin",))).role == "admin"
    with pytest.raises(NotFoundError):
        await svc.get_user_detail(987654)


# --- блокировка и подтверждение ---

async def test_ban_and_unban(svc, db_session, make_user):
    admin = await make_user("admin")
    user = await make_user(password="password8")

    await svc.ban_user(user.id, "Спам", admin.id, "admin")

    assert user.is_active is False
    [entry] = await _audit(db_session, "user.ban")
    assert entry.payload == {"reason": "Спам"} and entry.actor_id == admin.id
    with pytest.raises(AuthenticationError):
        await AuthService(db_session).login(user.phone, "password8", None, None)

    await svc.unban_user(user.id, admin.id, "admin")

    assert user.is_active is True
    await AuthService(db_session).login(user.phone, "password8", None, None)


async def test_verify_and_unverify(svc, db_session, make_user):
    admin = await make_user("admin")
    user = await make_user(is_verified=False)

    await svc.verify_user(user.id, admin.id, "admin")
    assert user.is_verified is True
    await svc.unverify_user(user.id, admin.id, "admin")
    assert user.is_verified is False
    assert len(await _audit(db_session, "user.verify")) == 1 and len(await _audit(db_session, "user.unverify")) == 1


@pytest.mark.parametrize("action", ["ban", "unban", "verify", "unverify"])
async def test_senior_accounts_cannot_be_managed(svc, make_user, action):
    actor = await make_user("admin")
    for role in ("admin", "superadmin"):
        target = await make_user(role)
        call = {
            "ban": lambda: svc.ban_user(target.id, "x", actor.id, "admin"),
            "unban": lambda: svc.unban_user(target.id, actor.id, "admin"),
            "verify": lambda: svc.verify_user(target.id, actor.id, "admin"),
            "unverify": lambda: svc.unverify_user(target.id, actor.id, "admin"),
        }[action]
        with pytest.raises(PermissionDeniedError):
            await call()
        assert target.is_active is True


@pytest.mark.parametrize("action", ["ban", "unban", "verify", "unverify"])
async def test_unknown_user_is_not_found(svc, make_user, action):
    actor = await make_user("admin")
    call = {
        "ban": lambda: svc.ban_user(987654, "x", actor.id, "admin"),
        "unban": lambda: svc.unban_user(987654, actor.id, "admin"),
        "verify": lambda: svc.verify_user(987654, actor.id, "admin"),
        "unverify": lambda: svc.unverify_user(987654, actor.id, "admin"),
    }[action]
    with pytest.raises(NotFoundError):
        await call()


# --- служебные учётки ---

@pytest.mark.parametrize("action", ["ban", "verify", "delete"])
async def test_integration_account_cannot_be_blocked_or_deleted(svc, db_session, make_user, action):
    """Учётка интеграции с Bitrix заведена с ролью оператора, но сотрудником не
    является: её блокировка или удаление оборвали бы комментарии Bitrix-задач в
    чатах заявок."""
    actor = await make_user("admin")
    integration = await _system_user(make_user, db_session)
    call = {
        "ban": lambda: svc.ban_user(integration.id, "x", actor.id, "admin"),
        "verify": lambda: svc.verify_user(integration.id, actor.id, "admin"),
        "delete": lambda: svc.delete_operator(integration.id, actor.id, "admin"),
    }[action]

    with pytest.raises(PermissionDeniedError):
        await call()

    assert integration.is_active is True and integration.login == BITRIX_USER_LOGIN


# --- удаление сотрудников ---

async def test_deleted_operator_is_anonymized_and_loses_sessions(svc, db_session, make_user):
    admin = await make_user("admin")
    operator = await make_user("operator", phone=None, login="op-1", full_name="Оператор", password="password8")
    await AuthService(db_session).admin_login("op-1", "password8", None, None)
    operator_id = operator.id

    await svc.delete_operator(operator_id, admin.id, "admin")

    assert operator.is_active is False and operator.login == f"_deleted_{operator_id}"
    assert operator.full_name is None and operator.email is None
    sessions = (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == operator_id))).scalars().all()
    assert sessions and all(s.revoked_at is not None for s in sessions)
    assert operator_id not in [u.id for u in (await svc.list_users(role="operator", size=100)).items]
    assert len(await _audit(db_session, "user.delete_operator")) == 1


async def test_only_operators_are_deleted_by_the_operator_endpoint(svc, make_user):
    admin = await make_user("admin")
    customer = await make_user()

    with pytest.raises(PermissionDeniedError):
        await svc.delete_operator(customer.id, admin.id, "admin")
    with pytest.raises(NotFoundError):
        await svc.delete_operator(987654, admin.id, "admin")


async def test_delete_admin(svc, db_session, make_user):
    superadmin = await make_user("superadmin")
    admin = await make_user("admin", phone=None, login="adm-1")
    admin_id = admin.id

    await svc.delete_admin(admin_id, superadmin.id, "superadmin")

    assert admin.is_active is False and admin.login == f"_deleted_{admin_id}"
    assert len(await _audit(db_session, "user.delete_admin")) == 1


async def test_delete_admin_refuses_self_superadmins_and_non_admins(svc, make_user):
    superadmin = await make_user("superadmin")
    other_super = await make_user("superadmin")
    operator = await make_user("operator")

    for target in (superadmin, other_super, operator):
        with pytest.raises(PermissionDeniedError):
            await svc.delete_admin(target.id, superadmin.id, "superadmin")
    with pytest.raises(NotFoundError):
        await svc.delete_admin(987654, superadmin.id, "superadmin")


# --- пользователи шкафа ---

async def test_cabinet_users_with_personal_names(
    svc, db_session, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet,
):
    from app.repositories.cabinet import CabinetUserSettingsRepository
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id, admin_internal_name="Главный ШУ")
    via_project, direct = await make_user(), await make_user()
    await link_user_project(via_project, project)
    await link_user_cabinet(direct, cabinet)
    await CabinetUserSettingsRepository(db_session).upsert(direct.id, cabinet.id, {"custom_name": "Мой шкаф"})

    users = await svc.list_cabinet_users(cabinet.id)

    by_id = {u.user_id: u for u in users}
    assert by_id[via_project.id].custom_name == "Главный ШУ"
    assert by_id[direct.id].custom_name == "Мой шкаф"
    with pytest.raises(NotFoundError):
        await svc.list_cabinet_users(987654)
