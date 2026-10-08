"""AuthService: вход, refresh-токены, выход.

Ничего внешнего не трогает (Bitrix/Telegram тут вообще ни при чём) — реальная
БД, реальный bcrypt/JWT, никаких моков не нужно. Фокус — на протоколе
refresh-токена (одноразовость, окно гонки, детект кражи) и лимите сессий,
описанном в README («Конвенции API»), но никогда не покрытом тестом.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import AuthenticationError
from app.core.security import hash_token
from app.services.auth_service import AuthService, _REFRESH_REUSE_GRACE_SECONDS


@pytest.fixture
def auth(db_session):
    return AuthService(db_session)


# --- login ---

async def test_login_wrong_password_rejected(auth, make_user):
    user = await make_user(password="correct-horse-battery")
    with pytest.raises(AuthenticationError, match="Неверный телефон или пароль"):
        await auth.login(user.phone, "wrong-password", None, None)


async def test_login_unknown_phone_same_message_as_wrong_password(auth):
    # сообщение одинаковое для "нет такого номера" и "неверный пароль" намеренно —
    # иначе по ответу можно узнать, зарегистрирован ли номер в системе
    with pytest.raises(AuthenticationError, match="Неверный телефон или пароль"):
        await auth.login("+375290000000", "whatever", None, None)


async def test_login_inactive_account_rejected(auth, make_user):
    user = await make_user(password="pass12345", is_active=False)
    with pytest.raises(AuthenticationError, match="заблокирован"):
        await auth.login(user.phone, "pass12345", None, None)


async def test_login_unverified_phone_rejected(auth, make_user):
    user = await make_user(password="pass12345", is_phone_verified=False)
    with pytest.raises(AuthenticationError, match="не подтверждён"):
        await auth.login(user.phone, "pass12345", None, None)


async def test_login_success_returns_two_distinct_tokens(auth, make_user):
    user = await make_user(password="pass12345")
    access, refresh = await auth.login(user.phone, "pass12345", "pytest-agent", "127.0.0.1")
    assert access and refresh
    assert access != refresh


async def test_login_trims_sessions_beyond_five(auth, make_user, db_session):
    user = await make_user(password="pass12345")
    for _ in range(6):
        await auth.login(user.phone, "pass12345", None, None)

    active = [t for t in (await _all_tokens(db_session, user.id)) if t.revoked_at is None]
    assert len(active) == 5


async def _all_tokens(db_session, user_id):
    from sqlalchemy import select
    from app.models.refresh_token import RefreshToken
    rows = (await db_session.execute(
        select(RefreshToken).where(RefreshToken.user_id == user_id).order_by(RefreshToken.created_at)
    )).scalars().all()
    return rows


# --- refresh: обычная ротация ---

async def test_refresh_rotates_token_and_revokes_old(auth, make_user, db_session):
    user = await make_user(password="pass12345")
    _, refresh = await auth.login(user.phone, "pass12345", None, None)

    new_access, new_refresh = await auth.refresh_tokens(refresh, None, None)

    assert new_refresh != refresh
    tokens = await _all_tokens(db_session, user.id)
    assert len(tokens) == 2
    old = next(t for t in tokens if t.last_used_at is not None or t.revoked_at is not None)
    assert old.revoked_at is not None
    assert old.replaced_by_id is not None


async def test_refresh_unknown_token_rejected(auth):
    with pytest.raises(AuthenticationError, match="не найден"):
        await auth.refresh_tokens("this-token-does-not-exist", None, None)


async def test_refresh_expired_token_rejected(auth, make_user, db_session):
    user = await make_user(password="pass12345")
    from app.core.security import generate_refresh_token, hash_token
    from app.models.refresh_token import RefreshToken

    raw = generate_refresh_token()
    token = RefreshToken(
        user_id=user.id, token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # уже истёк
    )
    db_session.add(token)
    await db_session.flush()

    with pytest.raises(AuthenticationError, match="истёк"):
        await auth.refresh_tokens(raw, None, None)


async def test_refresh_inactive_user_rejected(auth, make_user):
    user = await make_user(password="pass12345")
    _, refresh = await auth.login(user.phone, "pass12345", None, None)
    user.is_active = False

    with pytest.raises(AuthenticationError, match="недоступен"):
        await auth.refresh_tokens(refresh, None, None)


# --- refresh: повторное использование уже отозванного токена ---

async def test_refresh_reused_within_grace_window_issues_fresh_pair(auth, make_user):
    # несколько вкладок/гонка — не кража: обе параллельные попытки должны
    # получить рабочую пару токенов, а не вылететь из системы
    user = await make_user(password="pass12345")
    _, refresh = await auth.login(user.phone, "pass12345", None, None)

    await auth.refresh_tokens(refresh, None, None)  # первая ротация — успешна
    # повтор СТАРЫМ (уже отозванным) токеном сразу же — должен тоже пройти
    new_access, new_refresh = await auth.refresh_tokens(refresh, None, None)
    assert new_access and new_refresh


async def test_refresh_reused_after_grace_window_is_treated_as_compromise(auth, make_user, db_session, monkeypatch):
    user = await make_user(password="pass12345")
    _, refresh = await auth.login(user.phone, "pass12345", None, None)
    await auth.refresh_tokens(refresh, None, None)  # ротация, старый токен отозван

    # имитируем, что с момента ротации прошло больше grace-окна
    from sqlalchemy import select
    from app.models.refresh_token import RefreshToken
    stored = (await db_session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh))
    )).scalar_one()
    stored.revoked_at = datetime.now(timezone.utc) - timedelta(seconds=_REFRESH_REUSE_GRACE_SECONDS + 5)
    await db_session.flush()

    with pytest.raises(AuthenticationError, match="скомпрометирован"):
        await auth.refresh_tokens(refresh, None, None)

    # все сессии пользователя должны были завершиться, не только эта
    tokens = await _all_tokens(db_session, user.id)
    assert all(t.revoked_at is not None for t in tokens)


async def test_refresh_token_revoked_by_logout_is_always_treated_as_compromise(auth, make_user, db_session):
    # у отозванного через logout токена нет replaced_by_id — защита от гонки
    # вкладок на него не распространяется ни при каких условиях, даже сразу после
    user = await make_user(password="pass12345")
    _, refresh = await auth.login(user.phone, "pass12345", None, None)
    await auth.logout(refresh)

    with pytest.raises(AuthenticationError, match="скомпрометирован"):
        await auth.refresh_tokens(refresh, None, None)


# --- logout ---

async def test_logout_revokes_token(auth, make_user, db_session):
    user = await make_user(password="pass12345")
    _, refresh = await auth.login(user.phone, "pass12345", None, None)
    await auth.logout(refresh)

    tokens = await _all_tokens(db_session, user.id)
    assert tokens[0].revoked_at is not None


async def test_logout_unknown_token_does_not_raise(auth):
    await auth.logout("never-issued-token")  # просто не должно падать


async def test_logout_already_revoked_token_is_idempotent(auth, make_user):
    user = await make_user(password="pass12345")
    _, refresh = await auth.login(user.phone, "pass12345", None, None)
    await auth.logout(refresh)
    await auth.logout(refresh)  # повторный логаут тем же токеном — не должен падать
