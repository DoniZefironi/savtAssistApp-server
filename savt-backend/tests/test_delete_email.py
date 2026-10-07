"""DELETE /auth/me/email — отдельная ручка очистки почты: через PATCH /auth/me
сделать это нельзя, там email=None означает «не менять».
"""
from app.services.auth_service import AuthService


async def test_delete_email_clears_it(db_session, make_user):
    user = await make_user(email="ivanov@example.com")

    result = await AuthService(db_session).delete_email(user)

    assert result.email is None


async def test_delete_email_is_idempotent(db_session, make_user):
    user = await make_user(email=None)

    result = await AuthService(db_session).delete_email(user)

    assert result.email is None


async def test_update_profile_with_none_email_keeps_existing(db_session, make_user):
    # ровно причина, по которой нужна отдельная ручка
    user = await make_user(email="ivanov@example.com")

    result = await AuthService(db_session).update_profile(
        user=user, full_name=None, email=None, organization_name=None,
    )

    assert result.email == "ivanov@example.com"


async def test_deleted_email_can_be_reused_by_another_user(db_session, make_user):
    first = await make_user(email="shared@example.com")
    await AuthService(db_session).delete_email(first)

    second = await make_user(email="shared@example.com")

    assert second.email == "shared@example.com"
