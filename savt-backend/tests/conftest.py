import os

# Переменные окружения — ДО любого импорта из app.*, т.к. app.config.settings
# читается один раз при первом импорте модуля.
#
# DATABASE_URL — отдельная тестовая БД, никогда не дев/прод (см. tests/README.md
# про то, как её поднять и прогнать миграции).
#
# BITRIX_WEBHOOK_URL — пустая строка нарочно: это страховка, не основная защита
# (основная — явный мок в mock_bitrix ниже). Если какой-то путь в коде вызовет
# bitrix_service без мока, функции там же сами проверяют
# "if not settings.bitrix_webhook_url: return None" и тихо ничего не делают,
# вместо похода в реальный Bitrix. Решение не писать туда из тестов не
# техническое — его явно попросил пользователь, см. память по проекту.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5433/savt_test")
os.environ.setdefault("BITRIX_WEBHOOK_URL", "")

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.security import hash_password
from app.models.cabinets import Cabinet
from app.models.chat import Chat
from app.models.document import Document
from app.models.project import Project
from app.models.reclamation import Reclamation
from app.models.role import Role
from app.models.user import User
from app.models.user_cabinet import UserCabinet
from app.models.user_project import UserProject


@pytest_asyncio.fixture
async def db_session():
    """Сессия в отдельном SAVEPOINT поверх внешней транзакции — всё, что сделал
    тест (и всё, что сделал сервисный код его собственными session.commit()),
    откатывается целиком после теста. NullPool — чтобы не делить пул соединений
    между тестами, запускаемыми параллельно (pytest-xdist), если до этого дойдёт.

    Паттерн — официальная рекомендация SQLAlchemy 2.0 для тестов ("Joining a
    Session into an External Transaction"): session.commit() внутри
    тестируемого кода на самом деле коммитит только текущий SAVEPOINT,
    после чего слушатель ниже сразу открывает новый — внешняя транзакция
    остаётся незакоммиченной до explicit rollback в конце фикстуры."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        outer_trans = await conn.begin()
        TestSessionLocal = async_sessionmaker(bind=conn, expire_on_commit=False)
        session = TestSessionLocal()
        nested = await conn.begin_nested()

        @event.listens_for(session.sync_session, "after_transaction_end")
        def _restart_savepoint(sess, transaction):
            nonlocal nested
            if not nested.is_active:
                nested = conn.sync_connection.begin_nested()

        try:
            yield session
        finally:
            await session.close()
            await outer_trans.rollback()
    await engine.dispose()


@pytest.fixture
def mock_bitrix(monkeypatch):
    """Подменяет ВСЕ пишущие функции bitrix_service — ничего из тестов не
    должно реально улетать в Bitrix (см. BITRIX_WEBHOOK_URL выше и память
    проекта: пользователь явно попросил не трогать Bitrix автотестами).
    Возвращает список вызовов (name, args, kwargs) для проверки в assert —
    тест смотрит не "ушло ли в Bitrix", а "вызвал ли наш код нужную функцию
    с нужными аргументами в нужный момент".

    Читающие функции (get_reclamation_item и т.п.) здесь не трогаются —
    кто вызывает их напрямую, мокает сам в своём тесте."""
    calls: list[tuple[str, tuple, dict]] = []

    def _record(name):
        async def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            if name == "create_reclamation_item":
                return "999999"  # фейковый item_id, чтобы код мог пойти дальше
            return None
        return _fn

    for fn_name in (
        "create_reclamation_item",
        "update_reclamation_stage",
        "update_reclamation_warranty",
        "update_reclamation_deadline",
        "update_reclamation_assignee",
        "add_reclamation_comment",
    ):
        monkeypatch.setattr(f"app.services.bitrix_service.{fn_name}", _record(fn_name))

    return calls


@pytest_asyncio.fixture
async def make_user(db_session: AsyncSession):
    """Фабрика тестовых пользователей — роль 'user' по умолчанию (роли 1/2/3
    уже в базе, см. миграцию bebb51c938d0, create_all тут не используется).

    password — если передан, хэшируется настоящим bcrypt (нужно тестам
    логина/токенов, которые реально сверяют пароль); без него — фиктивный хэш,
    достаточно для тестов, где пароль не проверяется."""
    counter = {"n": 0}

    async def _make(role_name: str = "user", password: str | None = None, **overrides) -> User:
        counter["n"] += 1
        role = (await db_session.execute(
            Role.__table__.select().where(Role.name == role_name)
        )).first()
        assert role is not None, f"роль {role_name!r} не найдена — миграции применены?"
        defaults = dict(
            phone=f"+37529000{counter['n']:04d}",
            full_name=f"Тестовый Пользователь {counter['n']}",
            hashed_password=hash_password(password) if password else "not-a-real-hash",
            role_id=role.id,
            is_phone_verified=True,
            is_verified=True,
        )
        defaults.update(overrides)
        user = User(**defaults)
        db_session.add(user)
        await db_session.flush()
        return user

    return _make


@pytest_asyncio.fixture
async def make_project(db_session: AsyncSession):
    counter = {"n": 0}

    async def _make(**overrides) -> Project:
        counter["n"] += 1
        defaults = dict(
            name=f"Тестовый проект {counter['n']}",
            unique_code=f"test-code-{counter['n']}",
        )
        defaults.update(overrides)
        project = Project(**defaults)
        db_session.add(project)
        await db_session.flush()
        return project

    return _make


@pytest_asyncio.fixture
async def make_cabinet(db_session: AsyncSession):
    counter = {"n": 0}

    async def _make(**overrides) -> Cabinet:
        counter["n"] += 1
        defaults = dict(
            type="ШУ-18К",
            object_number=f"29_{counter['n']:03d}",
        )
        defaults.update(overrides)
        cabinet = Cabinet(**defaults)
        db_session.add(cabinet)
        await db_session.flush()
        return cabinet

    return _make


@pytest_asyncio.fixture
async def link_user_project(db_session: AsyncSession):
    """Привязывает пользователя к проекту (UserProject) — доступ к ШУ выводится
    только отсюда, см. CabinetRepository.get_accessible_for_user."""
    async def _link(user: User, project: Project, **overrides) -> UserProject:
        defaults = dict(user_id=user.id, project_id=project.id)
        defaults.update(overrides)
        up = UserProject(**defaults)
        db_session.add(up)
        await db_session.flush()
        return up

    return _link


@pytest_asyncio.fixture
async def link_user_cabinet(db_session: AsyncSession):
    """Прямое владение ШУ в обход проекта (UserCabinet) — второй путь доступа,
    см. CabinetRepository.get_accessible_for_user."""
    async def _link(user: User, cabinet: Cabinet, **overrides) -> UserCabinet:
        defaults = dict(user_id=user.id, cabinet_id=cabinet.id)
        defaults.update(overrides)
        uc = UserCabinet(**defaults)
        db_session.add(uc)
        await db_session.flush()
        return uc

    return _link


@pytest_asyncio.fixture
async def make_chat(db_session: AsyncSession):
    async def _make(user: User, chat_type: str = "cabinet", **overrides) -> Chat:
        chat = Chat(user_id=user.id, chat_type=chat_type, **overrides)
        db_session.add(chat)
        await db_session.flush()
        return chat

    return _make


@pytest_asyncio.fixture
async def make_document(db_session: AsyncSession, make_project):
    counter = {"n": 0}

    async def _make(**overrides) -> Document:
        counter["n"] += 1
        if "cabinet_id" not in overrides and "project_id" not in overrides:
            overrides["project_id"] = (await make_project()).id
        defaults = dict(
            doc_type="manual",
            title=f"Документ {counter['n']}",
            file_url=f"/static/documents/test-{counter['n']}.pdf",
            file_size_bytes=1024,
            mime_type="application/pdf",
        )
        defaults.update(overrides)
        doc = Document(**defaults)
        db_session.add(doc)
        await db_session.flush()
        return doc

    return _make


@pytest_asyncio.fixture
async def make_reclamation(db_session: AsyncSession, make_user, make_project):
    """Фабрика рекламации с минимально необходимыми полями (object_type=cabinet
    требует cabinet_id — для тестов, где это не важно, проще всего остаться на
    object_type='line' + project_id, чтобы не тащить ещё и Cabinet)."""
    async def _make(**overrides) -> Reclamation:
        user = overrides.pop("user", None) or await make_user()
        project_id = overrides.pop("project_id", None)
        if project_id is None and "cabinet_id" not in overrides:
            project_id = (await make_project()).id
        defaults = dict(
            user_id=user.id,
            status="new",
            object_type="line",
            project_id=project_id,
            description="Тестовая неисправность",
            contact_name="Иванов Иван",
            contact_phone="+375291234567",
            contact_email="test@example.com",
        )
        defaults.update(overrides)
        rec = Reclamation(**defaults)
        db_session.add(rec)
        await db_session.flush()
        return rec

    return _make


@pytest_asyncio.fixture
async def api(db_session):
    """HTTP-клиент к настоящему приложению (ASGI, без сети): обработчики, права и
    проверка токенов — как в бою, но на тестовой сессии, поэтому созданное
    тестом пользователи и данные ему видны, а после теста всё откатывается."""
    import httpx
    from app.core.dependencies import get_session
    from app.main import app

    async def override():
        yield db_session

    app.dependency_overrides[get_session] = override
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def tokens(make_user):
    """Токен доступа на каждую роль: {"user": ..., "operator": ..., ...}."""
    from app.core.security import create_access_token

    result = {}
    for role in ("user", "operator", "admin", "superadmin"):
        user = await make_user(role)
        result[role] = create_access_token(user_id=user.id, role=role)
    return result
