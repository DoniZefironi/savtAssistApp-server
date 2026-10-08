"""Поля ответов админских ручек, которые читает фронтенд админки: подпись в
ленте дашборда, телефон заявителя в чатах оператора, срок гарантии в точках
карты ШУ, логин в профиле."""
from datetime import datetime, timezone

from app.core.constants import RoleName
from app.models.phone_change_request import PhoneChangeRequest
from app.models.service_request import ServiceRequest
from app.routers import auth as auth_router
from app.services.cabinet_service import CabinetService
from app.services.chat_service import ChatService
from app.services.dashboard_service import DashboardService


async def test_dashboard_activity_has_short_detail(db_session, make_user, make_project):
    user = await make_user(full_name="Иванов Иван")
    project = await make_project()
    db_session.add_all([
        PhoneChangeRequest(user_id=user.id, new_phone="+375291234567", status="pending"),
        ServiceRequest(
            user_id=user.id, project_id=project.id, request_type="repair", is_under_warranty=False,
            description="Не включается   вентилятор\nприточной установки " + "очень " * 30,
        ),
    ])
    await db_session.flush()

    operator = await make_user("operator")
    dashboard = await DashboardService(db_session).get_dashboard(operator.id)

    by_type = {item.type: item for item in dashboard.recent_activity}
    assert by_type["phone_change"].detail == "+375291234567"
    service_detail = by_type["service"].detail
    assert service_detail.startswith("Не включается вентилятор приточной установки")
    assert "\n" not in service_detail and len(service_detail) <= 80 and service_detail.endswith("…")


async def test_operator_chat_list_has_applicant_phone(db_session, make_user, make_project, make_chat):
    applicant = await make_user(full_name="Сидоров Семён", phone="+375291119999")
    project = await make_project()
    chat = await make_chat(applicant, chat_type="project", project_id=project.id)
    operator = await make_user("operator")

    chats = await ChatService(db_session).list_operator_chats(operator.id)

    item = next(c for c in chats if c.id == chat.id)
    assert item.user_name == "Сидоров Семён"
    assert item.user_phone == "+375291119999"


async def test_geo_points_have_warranty_end_date(db_session, make_cabinet):
    ends = datetime(2027, 1, 1, tzinfo=timezone.utc)
    cabinet = await make_cabinet(warranty_ends_at=ends, latitude=53.9, longitude=27.5)

    points = await CabinetService(db_session).get_geo()

    point = next(p for p in points if p.id == cabinet.id)
    assert point.warranty_ends_at == ends


async def test_me_returns_login_for_staff_and_null_for_user(db_session, make_user):
    staff = await make_user("operator", phone=None, login="operator7")
    customer = await make_user()

    staff_me = await auth_router.me(user=staff, session=db_session)
    customer_me = await auth_router.me(user=customer, session=db_session)

    assert staff_me.login == "operator7"
    assert customer_me.login is None
    assert staff_me.role == RoleName.OPERATOR.value
