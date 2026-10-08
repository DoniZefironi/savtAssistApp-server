"""Админский список заявок на сброс пароля отдаёт данные заявителя для карточки:
подтверждён ли он и когда зарегистрирован — те же поля, что у заявок на смену
номера, документы и ШУ."""
from app.models.password_reset_request import PasswordResetRequest
from app.services.password_reset_request_service import PasswordResetRequestService


async def test_list_has_user_verification_and_registration_date(db_session, make_user):
    verified = await make_user(full_name="Подтверждённый Пётр", is_verified=True)
    unverified = await make_user(full_name="Неподтверждённый Иван", is_verified=False)
    db_session.add_all([
        PasswordResetRequest(user_id=verified.id, hashed_password="x"),
        PasswordResetRequest(user_id=unverified.id, hashed_password="x"),
    ])
    await db_session.flush()

    page = await PasswordResetRequestService(db_session).list_requests()

    by_user = {item.user_id: item for item in page.items}
    assert by_user[verified.id].user_is_verified is True
    assert by_user[unverified.id].user_is_verified is False
    assert by_user[verified.id].user_registered_at == verified.created_at
