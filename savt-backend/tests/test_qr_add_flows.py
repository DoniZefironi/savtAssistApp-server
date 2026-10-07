"""Добавление проекта и ШУ по QR — без чьего-либо одобрения, плюс слияние:
если пользователь сначала добавил ШУ отдельно, а потом проект, которому этот ШУ
принадлежит, прямая привязка снимается, а чат и история остаются на месте.
Заодно разбор кода из QR (savt://, публичная страница, голый код) и самостоятельное
снятие своего отдельно добавленного ШУ.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.chat import Chat
from app.models.notification import Notification
from app.models.user_cabinet import UserCabinet
from app.models.user_project import UserProject
from app.repositories.cabinet import CabinetRepository
from app.schemas.cabinet import AddCabinetByQrIn
from app.schemas.project import AddProjectByQrIn
from app.services.user_cabinet_service import UserCabinetService
from app.services.user_project_service import UserProjectService


async def _notifications(db_session, user_id):
    result = await db_session.execute(select(Notification).where(Notification.user_id == user_id))
    return list(result.scalars().all())


# --- разбор кода из QR ---

@pytest.mark.parametrize("raw, expected", [
    ("savt://project/ABC123", "ABC123"),
    ("https://helper.savt.by/add/project/ABC123", "ABC123"),
    ("https://helper.savt.by//add/project/ABC123", "ABC123"),
    ("ABC123", "ABC123"),
])
def test_project_code_parsed_from_any_format(raw, expected):
    assert AddProjectByQrIn(qr_data=raw).parse_unique_code() == expected


@pytest.mark.parametrize("raw, expected", [
    ("savt://cabinet/XyZ789", "XyZ789"),
    ("https://helper.savt.by/add/cabinet/XyZ789", "XyZ789"),
    ("XyZ789", "XyZ789"),
])
def test_cabinet_code_parsed_from_any_format(raw, expected):
    assert AddCabinetByQrIn(qr_data=raw).parse_unique_code() == expected


# --- добавление ШУ по QR ---

async def test_add_cabinet_by_qr_creates_direct_binding(db_session, make_user, make_cabinet):
    user = await make_user()
    cabinet = await make_cabinet(project_id=None, unique_code="cab-code-1")

    result = await UserCabinetService(db_session).add_by_qr(user.id, "cab-code-1")

    assert result["status"] == "linked"
    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is True


async def test_add_cabinet_by_qr_unknown_code(db_session, make_user):
    user = await make_user()
    with pytest.raises(NotFoundError):
        await UserCabinetService(db_session).add_by_qr(user.id, "no-such-code")


async def test_add_cabinet_by_qr_soft_deleted_cabinet_not_found(db_session, make_user, make_cabinet):
    user = await make_user()
    await make_cabinet(unique_code="dead-code", deleted_at=datetime.now(timezone.utc))
    with pytest.raises(NotFoundError):
        await UserCabinetService(db_session).add_by_qr(user.id, "dead-code")


async def test_add_cabinet_by_qr_twice_conflicts(db_session, make_user, make_cabinet):
    user = await make_user()
    await make_cabinet(unique_code="cab-code-2")
    svc = UserCabinetService(db_session)
    await svc.add_by_qr(user.id, "cab-code-2")

    with pytest.raises(AlreadyExistsError):
        await svc.add_by_qr(user.id, "cab-code-2")


async def test_add_cabinet_by_qr_when_already_in_its_project_conflicts_and_creates_nothing(db_session, make_user, make_project, make_cabinet, link_user_project):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id, unique_code="cab-code-3")
    await link_user_project(user, project)

    with pytest.raises(AlreadyExistsError):
        await UserCabinetService(db_session).add_by_qr(user.id, "cab-code-3")

    rows = (await db_session.execute(select(UserCabinet).where(UserCabinet.cabinet_id == cabinet.id))).scalars().all()
    assert rows == []


async def test_add_cabinet_by_qr_allowed_for_cabinet_of_foreign_project(db_session, make_user, make_project, make_cabinet):
    user = await make_user()
    foreign = await make_project()
    cabinet = await make_cabinet(project_id=foreign.id, unique_code="cab-code-4")

    await UserCabinetService(db_session).add_by_qr(user.id, "cab-code-4")

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is True


# --- вступление в проект ---

async def test_add_project_by_qr_joins_immediately_without_approval(db_session, make_user, make_project):
    user = await make_user()
    project = await make_project(unique_code="proj-code-1")

    result = await UserProjectService(db_session).add_by_qr(user.id, "proj-code-1")

    assert result["status"] == "linked"
    member = (await db_session.execute(
        select(UserProject).where(UserProject.user_id == user.id, UserProject.project_id == project.id)
    )).scalar_one_or_none()
    assert member is not None


async def test_add_project_by_qr_creates_project_chat(db_session, make_user, make_project):
    user = await make_user()
    project = await make_project(unique_code="proj-code-2")

    await UserProjectService(db_session).add_by_qr(user.id, "proj-code-2")

    chat = (await db_session.execute(
        select(Chat).where(Chat.user_id == user.id, Chat.chat_type == "project", Chat.project_id == project.id)
    )).scalar_one_or_none()
    assert chat is not None


async def test_many_users_can_join_same_project(db_session, make_user, make_project):
    project = await make_project(unique_code="proj-code-3")
    users = [await make_user() for _ in range(3)]
    svc = UserProjectService(db_session)

    for user in users:
        await svc.add_by_qr(user.id, "proj-code-3")

    count = len((await db_session.execute(
        select(UserProject).where(UserProject.project_id == project.id)
    )).scalars().all())
    assert count == 3


async def test_add_project_by_qr_twice_conflicts(db_session, make_user, make_project):
    user = await make_user()
    await make_project(unique_code="proj-code-4")
    svc = UserProjectService(db_session)
    await svc.add_by_qr(user.id, "proj-code-4")

    with pytest.raises(AlreadyExistsError):
        await svc.add_by_qr(user.id, "proj-code-4")


async def test_add_project_by_qr_unknown_code(db_session, make_user):
    user = await make_user()
    with pytest.raises(NotFoundError):
        await UserProjectService(db_session).add_by_qr(user.id, "no-such-project")


async def test_add_project_by_qr_soft_deleted_project_not_found(db_session, make_user, make_project):
    user = await make_user()
    await make_project(unique_code="proj-dead", deleted_at=datetime.now(timezone.utc))
    with pytest.raises(NotFoundError):
        await UserProjectService(db_session).add_by_qr(user.id, "proj-dead")


# --- слияние отдельно добавленного ШУ с проектом ---

async def test_joining_project_removes_direct_binding_but_keeps_access(db_session, make_user, make_project, make_cabinet, link_user_cabinet):
    user = await make_user()
    project = await make_project(unique_code="merge-1")
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_cabinet(user, cabinet)

    await UserProjectService(db_session).add_by_qr(user.id, "merge-1")

    direct = (await db_session.execute(
        select(UserCabinet).where(UserCabinet.user_id == user.id, UserCabinet.cabinet_id == cabinet.id)
    )).scalar_one_or_none()
    assert direct is None
    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is True


async def test_merge_keeps_cabinet_chat_untouched(db_session, make_user, make_project, make_cabinet, link_user_cabinet, make_chat):
    user = await make_user()
    project = await make_project(unique_code="merge-2")
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_cabinet(user, cabinet)
    chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)

    await UserProjectService(db_session).add_by_qr(user.id, "merge-2")

    await db_session.refresh(chat)
    assert chat.archived_at is None
    assert chat.cabinet_id == cabinet.id


async def test_merge_notifies_user_once_per_merged_cabinet(db_session, make_user, make_project, make_cabinet, link_user_cabinet):
    user = await make_user()
    project = await make_project(unique_code="merge-3")
    first = await make_cabinet(project_id=project.id)
    second = await make_cabinet(project_id=project.id)
    await link_user_cabinet(user, first)
    await link_user_cabinet(user, second)

    await UserProjectService(db_session).add_by_qr(user.id, "merge-3")

    notes = [n for n in await _notifications(db_session, user.id) if n.title == "ШУ перенесён в проект"]
    assert len(notes) == 2
    assert {n.data["cabinet_id"] for n in notes} == {first.id, second.id}


async def test_merge_does_not_touch_cabinets_outside_the_project(db_session, make_user, make_project, make_cabinet, link_user_cabinet):
    user = await make_user()
    project = await make_project(unique_code="merge-4")
    outside = await make_cabinet(project_id=None)
    await link_user_cabinet(user, outside)

    await UserProjectService(db_session).add_by_qr(user.id, "merge-4")

    still_direct = (await db_session.execute(
        select(UserCabinet).where(UserCabinet.user_id == user.id, UserCabinet.cabinet_id == outside.id)
    )).scalar_one_or_none()
    assert still_direct is not None
    assert [n for n in await _notifications(db_session, user.id) if n.title == "ШУ перенесён в проект"] == []


async def test_joining_project_does_not_touch_other_users_direct_bindings(db_session, make_user, make_project, make_cabinet, link_user_cabinet):
    joiner = await make_user()
    other = await make_user()
    project = await make_project(unique_code="merge-5")
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_cabinet(other, cabinet)

    await UserProjectService(db_session).add_by_qr(joiner.id, "merge-5")

    other_binding = (await db_session.execute(
        select(UserCabinet).where(UserCabinet.user_id == other.id, UserCabinet.cabinet_id == cabinet.id)
    )).scalar_one_or_none()
    assert other_binding is not None


# --- пользователь сам убирает отдельно добавленный ШУ ---

async def test_user_removes_own_direct_cabinet_and_chat_is_archived(db_session, make_user, make_cabinet, link_user_cabinet, make_chat):
    user = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(user, cabinet)
    chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)

    await UserCabinetService(db_session).remove_cabinet(user.id, cabinet.id)

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is False
    await db_session.refresh(chat)
    assert chat.archived_at is not None


async def test_user_removal_does_not_archive_other_users_chat(db_session, make_user, make_cabinet, link_user_cabinet, make_chat):
    user = await make_user()
    other = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(user, cabinet)
    await link_user_cabinet(other, cabinet)
    others_chat = await make_chat(other, "cabinet", cabinet_id=cabinet.id)

    await UserCabinetService(db_session).remove_cabinet(user.id, cabinet.id)

    await db_session.refresh(others_chat)
    assert others_chat.archived_at is None


async def test_user_cannot_remove_cabinet_that_comes_through_project(db_session, make_user, make_project, make_cabinet, link_user_project):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(user, project)

    with pytest.raises(AlreadyExistsError):
        await UserCabinetService(db_session).remove_cabinet(user.id, cabinet.id)


async def test_user_removing_unrelated_cabinet_is_not_found(db_session, make_user, make_cabinet):
    user = await make_user()
    cabinet = await make_cabinet(project_id=None)

    with pytest.raises(NotFoundError):
        await UserCabinetService(db_session).remove_cabinet(user.id, cabinet.id)
