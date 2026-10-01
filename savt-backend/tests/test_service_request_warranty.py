"""ServiceRequestService.create() — is_under_warranty: снимок гарантии ШУ/
проекта на момент создания заявки, напрямую определяет платно/бесплатно
обслуживание. Сквозной тест через реальный сервис (не только формулу отдельно)
— Bitrix-синхронизация внутри create() безопасна в тестах сама по себе:
create_task() не делает реального вызова без настроенного BITRIX_WEBHOOK_URL
(пустой во всех тестах, см. conftest.py), early-return до похода в БД.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.service_request import ServiceRequest
from app.schemas.service_requests import ServiceRequestCreateIn
from app.services.service_request_service import ServiceRequestService


@pytest.fixture
def svc(db_session):
    return ServiceRequestService(db_session)


async def _accessible_cabinet(make_user, make_project, make_cabinet, link_user_project, **cabinet_overrides):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id, **cabinet_overrides)
    await link_user_project(user, project)
    return user, cabinet


async def test_active_cabinet_warranty_is_under_warranty_true(svc, make_user, make_project, make_cabinet, link_user_project):
    user, cabinet = await _accessible_cabinet(
        make_user, make_project, make_cabinet, link_user_project,
        warranty_ends_at=datetime.now(timezone.utc) + timedelta(days=365),
    )
    req = await svc.create(user.id, ServiceRequestCreateIn(
        cabinet_id=cabinet.id, request_type="repair", description="Не работает кнопка управления",
    ))
    assert req.is_under_warranty is True


async def test_expired_cabinet_warranty_is_under_warranty_false(svc, make_user, make_project, make_cabinet, link_user_project):
    user, cabinet = await _accessible_cabinet(
        make_user, make_project, make_cabinet, link_user_project,
        warranty_ends_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    req = await svc.create(user.id, ServiceRequestCreateIn(
        cabinet_id=cabinet.id, request_type="repair", description="Не работает кнопка управления",
    ))
    assert req.is_under_warranty is False


async def test_no_warranty_date_is_under_warranty_false(svc, make_user, make_project, make_cabinet, link_user_project):
    user, cabinet = await _accessible_cabinet(
        make_user, make_project, make_cabinet, link_user_project, warranty_ends_at=None,
    )
    req = await svc.create(user.id, ServiceRequestCreateIn(
        cabinet_id=cabinet.id, request_type="repair", description="Не работает кнопка управления",
    ))
    assert req.is_under_warranty is False


async def test_expiring_soon_cabinet_warranty_still_counts_as_under_warranty(svc, make_user, make_project, make_cabinet, link_user_project):
    user, cabinet = await _accessible_cabinet(
        make_user, make_project, make_cabinet, link_user_project,
        warranty_ends_at=datetime.now(timezone.utc) + timedelta(days=5),
    )
    req = await svc.create(user.id, ServiceRequestCreateIn(
        cabinet_id=cabinet.id, request_type="repair", description="Не работает кнопка управления",
    ))
    assert req.is_under_warranty is True


async def test_project_level_request_uses_project_warranty(svc, make_user, make_project, link_user_project):
    user = await make_user()
    project = await make_project(warranty_ends_at=datetime.now(timezone.utc) + timedelta(days=365))
    await link_user_project(user, project)

    req = await svc.create(user.id, ServiceRequestCreateIn(
        project_id=project.id, request_type="diagnostics", description="Проблема по всему проекту",
    ))
    assert req.is_under_warranty is True


async def test_is_under_warranty_is_a_snapshot_not_recomputed_later(svc, db_session, make_user, make_project, make_cabinet, link_user_project):
    # комментарий в модели: "если администратор позже продлит гарантию
    # задним числом, уже созданная негарантийная заявка должна остаться
    # негарантийной" — фиксируем это поведение явно
    user, cabinet = await _accessible_cabinet(
        make_user, make_project, make_cabinet, link_user_project,
        warranty_ends_at=datetime.now(timezone.utc) - timedelta(days=1),  # истекла
    )
    req = await svc.create(user.id, ServiceRequestCreateIn(
        cabinet_id=cabinet.id, request_type="repair", description="Не работает кнопка управления",
    ))
    assert req.is_under_warranty is False

    # администратор продлевает гарантию задним числом
    cabinet.warranty_ends_at = datetime.now(timezone.utc) + timedelta(days=365)
    await db_session.flush()

    refreshed = await db_session.get(ServiceRequest, req.id)
    assert refreshed.is_under_warranty is False  # не пересчиталось
