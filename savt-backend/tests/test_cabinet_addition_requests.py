"""CabinetRequestService — заявки на добавление ШУ по фото заводской
таблички. Одобрение — это не просто смена статуса: если у заявки указан
project_id, тем же действием чинится принадлежность шкафа (ничейный ШУ
привязывается к проекту заявителя), а чужой проект — конфликт, не тихая
перепривязка.
"""
from datetime import datetime, timezone

import pytest

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.schemas.requests import ApproveAdditionIn, RejectRequestIn
from app.services.cabinet_request_service import CabinetRequestService


@pytest.fixture
def svc(db_session):
    return CabinetRequestService(db_session)


async def _make_addition_request(db_session, user, **overrides):
    defaults = dict(user_id=user.id, photo_url="/static/photos/plate.jpg")
    defaults.update(overrides)
    req = CabinetAdditionRequest(**defaults)
    db_session.add(req)
    await db_session.flush()
    return req


# --- approve_addition ---

async def test_approve_unknown_request_raises(svc, make_user):
    admin = await make_user(role_name="admin")
    with pytest.raises(NotFoundError):
        await svc.approve_addition(999999, ApproveAdditionIn(cabinet_id=1), admin.id, "admin")


async def test_approve_already_resolved_request_raises(svc, db_session, make_user, make_cabinet):
    user = await make_user()
    admin = await make_user(role_name="admin")
    cabinet = await make_cabinet()
    req = await _make_addition_request(db_session, user)
    await svc.approve_addition(req.id, ApproveAdditionIn(cabinet_id=cabinet.id), admin.id, "admin")

    with pytest.raises(AlreadyExistsError, match="уже обработана"):
        await svc.approve_addition(req.id, ApproveAdditionIn(cabinet_id=cabinet.id), admin.id, "admin")


async def test_approve_unknown_cabinet_raises(svc, db_session, make_user):
    user = await make_user()
    admin = await make_user(role_name="admin")
    req = await _make_addition_request(db_session, user)

    with pytest.raises(NotFoundError, match="ШУ не найден"):
        await svc.approve_addition(req.id, ApproveAdditionIn(cabinet_id=999999), admin.id, "admin")


async def test_approve_soft_deleted_cabinet_raises(svc, db_session, make_user, make_cabinet):
    user = await make_user()
    admin = await make_user(role_name="admin")
    cabinet = await make_cabinet(deleted_at=datetime.now(timezone.utc))
    req = await _make_addition_request(db_session, user)

    with pytest.raises(NotFoundError, match="ШУ не найден"):
        await svc.approve_addition(req.id, ApproveAdditionIn(cabinet_id=cabinet.id), admin.id, "admin")


async def test_approve_attaches_ownerless_cabinet_to_requested_project(svc, db_session, make_user, make_project, make_cabinet):
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    cabinet = await make_cabinet(project_id=None)  # ничейный
    req = await _make_addition_request(db_session, user, project_id=project.id)

    await svc.approve_addition(req.id, ApproveAdditionIn(cabinet_id=cabinet.id), admin.id, "admin")

    await db_session.refresh(cabinet)
    assert cabinet.project_id == project.id


async def test_approve_cabinet_already_in_requested_project_is_fine(svc, db_session, make_user, make_project, make_cabinet):
    # шкаф уже в ТОМ ЖЕ проекте — ничего не конфликтует, просто подтверждаем
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    req = await _make_addition_request(db_session, user, project_id=project.id)

    await svc.approve_addition(req.id, ApproveAdditionIn(cabinet_id=cabinet.id), admin.id, "admin")

    await db_session.refresh(req)
    assert req.status == "approved"


async def test_approve_cabinet_already_in_different_project_raises(svc, db_session, make_user, make_project, make_cabinet):
    user = await make_user()
    admin = await make_user(role_name="admin")
    requested_project = await make_project()
    other_project = await make_project()
    cabinet = await make_cabinet(project_id=other_project.id)  # уже в ДРУГОМ проекте
    req = await _make_addition_request(db_session, user, project_id=requested_project.id)

    with pytest.raises(AlreadyExistsError, match="уже принадлежит другому проекту"):
        await svc.approve_addition(req.id, ApproveAdditionIn(cabinet_id=cabinet.id), admin.id, "admin")

    await db_session.refresh(cabinet)
    assert cabinet.project_id == other_project.id  # не перепривязался молча


async def test_approve_without_project_id_does_not_touch_cabinet_project(svc, db_session, make_user, make_cabinet):
    # заявка без project_id (старый формат, см. комментарий в модели) —
    # принадлежность ШУ approve_addition не трогает вовсе
    user = await make_user()
    admin = await make_user(role_name="admin")
    cabinet = await make_cabinet(project_id=None)
    req = await _make_addition_request(db_session, user, project_id=None)

    await svc.approve_addition(req.id, ApproveAdditionIn(cabinet_id=cabinet.id), admin.id, "admin")

    await db_session.refresh(cabinet)
    assert cabinet.project_id is None


# --- reject_addition ---

async def test_reject_unknown_request_raises(svc, make_user):
    admin = await make_user(role_name="admin")
    with pytest.raises(NotFoundError):
        await svc.reject_addition(999999, RejectRequestIn(admin_response="причина"), admin.id, "admin")


async def test_reject_does_not_attach_cabinet(svc, db_session, make_user, make_project, make_cabinet):
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    cabinet = await make_cabinet(project_id=None)
    req = await _make_addition_request(db_session, user, project_id=project.id)

    await svc.reject_addition(req.id, RejectRequestIn(admin_response="фото не читается"), admin.id, "admin")

    await db_session.refresh(cabinet)
    assert cabinet.project_id is None
    await db_session.refresh(req)
    assert req.status == "rejected"
