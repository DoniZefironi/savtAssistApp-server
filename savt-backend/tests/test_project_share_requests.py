"""ProjectRequestService — заявки на вступление в проект. Одобрение сразу
даёт доступ ко ВСЕМ шкафам проекта (доступ выводится из членства, не
по-шкафно) — см. test_project_access.py для самой модели доступа, здесь —
про сам процесс одобрения/отклонения заявки.
"""
import pytest

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.project_share_request import ProjectShareRequest
from app.repositories.project import UserProjectRepository
from app.schemas.requests import ApproveShareIn, RejectRequestIn
from app.services.project_request_service import ProjectRequestService


@pytest.fixture
def svc(db_session):
    return ProjectRequestService(db_session)


async def _make_share_request(db_session, user, project, **overrides):
    defaults = dict(user_id=user.id, project_id=project.id)
    defaults.update(overrides)
    req = ProjectShareRequest(**defaults)
    db_session.add(req)
    await db_session.flush()
    return req


# --- approve_share ---

async def test_approve_unknown_request_raises(svc, make_user):
    admin = await make_user(role_name="admin")
    with pytest.raises(NotFoundError):
        await svc.approve_share(999999, ApproveShareIn(), admin.id, "admin")


async def test_approve_already_resolved_request_raises(svc, db_session, make_user, make_project):
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    req = await _make_share_request(db_session, user, project)
    await svc.approve_share(req.id, ApproveShareIn(), admin.id, "admin")

    with pytest.raises(AlreadyExistsError, match="уже обработана"):
        await svc.approve_share(req.id, ApproveShareIn(), admin.id, "admin")


async def test_approve_for_soft_deleted_project_raises(svc, db_session, make_user, make_project):
    from datetime import datetime, timezone
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project(deleted_at=datetime.now(timezone.utc))
    req = await _make_share_request(db_session, user, project)

    with pytest.raises(NotFoundError, match="Проект не найден"):
        await svc.approve_share(req.id, ApproveShareIn(), admin.id, "admin")


async def test_approve_when_already_a_member_raises(svc, db_session, make_user, make_project, link_user_project):
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    await link_user_project(user, project)  # уже состоит
    req = await _make_share_request(db_session, user, project)

    with pytest.raises(AlreadyExistsError, match="уже привязан"):
        await svc.approve_share(req.id, ApproveShareIn(), admin.id, "admin")


async def test_approve_success_grants_membership_as_non_primary(svc, db_session, make_user, make_project):
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    req = await _make_share_request(db_session, user, project, user_comment="пустите в проект")

    await svc.approve_share(req.id, ApproveShareIn(admin_response="добро пожаловать"), admin.id, "admin")

    membership = await UserProjectRepository(db_session).find(user.id, project.id)
    assert membership is not None
    assert membership.is_primary is False  # вступил по заявке, не основатель проекта

    await db_session.refresh(req)
    assert req.status == "approved"
    assert req.admin_response == "добро пожаловать"


# --- reject_share ---

async def test_reject_unknown_request_raises(svc, make_user):
    admin = await make_user(role_name="admin")
    with pytest.raises(NotFoundError):
        await svc.reject_share(999999, RejectRequestIn(admin_response="причина"), admin.id, "admin")


async def test_reject_does_not_grant_membership(svc, db_session, make_user, make_project):
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    req = await _make_share_request(db_session, user, project)

    await svc.reject_share(req.id, RejectRequestIn(admin_response="не подтверждён как сотрудник"), admin.id, "admin")

    assert await UserProjectRepository(db_session).find(user.id, project.id) is None
    await db_session.refresh(req)
    assert req.status == "rejected"


async def test_reject_already_resolved_request_raises(svc, db_session, make_user, make_project):
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    req = await _make_share_request(db_session, user, project)
    await svc.reject_share(req.id, RejectRequestIn(admin_response="отказ"), admin.id, "admin")

    with pytest.raises(AlreadyExistsError, match="уже обработана"):
        await svc.reject_share(req.id, RejectRequestIn(admin_response="ещё раз"), admin.id, "admin")
