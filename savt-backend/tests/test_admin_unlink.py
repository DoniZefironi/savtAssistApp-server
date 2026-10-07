"""Админская отвязка: ШУ, добавленный пользователем отдельно (UserCabinet), и
исключение из проекта — пользователю уходит уведомление, его чаты по этому
объекту архивируются, остальных не трогаем. Плюс карточка пользователя в
админке: отдельно добавленные ШУ видны, шкафы проектов отдельно не перечисляются.
"""
import pytest
from sqlalchemy import select

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.user_cabinet import UserCabinet
from app.models.user_project import UserProject
from app.repositories.cabinet import CabinetRepository
from app.services.admin_user_service import AdminUserService
from app.services.project_service import ProjectService


async def _notes(db_session, user_id, title):
    rows = (await db_session.execute(select(Notification).where(Notification.user_id == user_id))).scalars().all()
    return [n for n in rows if n.title == title]


# --- AdminUserService.remove_user_from_cabinet ---

async def test_admin_unlinks_direct_cabinet(db_session, make_user, make_cabinet, link_user_cabinet):
    admin = await make_user("admin")
    user = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(user, cabinet)

    await AdminUserService(db_session).remove_user_from_cabinet(cabinet.id, user.id, "ошибочная привязка", admin.id, "admin")

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is False


async def test_admin_unlink_archives_only_that_users_cabinet_chats(db_session, make_user, make_cabinet, link_user_cabinet, make_chat):
    admin = await make_user("admin")
    user = await make_user()
    other = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(user, cabinet)
    await link_user_cabinet(other, cabinet)
    users_chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)
    others_chat = await make_chat(other, "cabinet", cabinet_id=cabinet.id)

    await AdminUserService(db_session).remove_user_from_cabinet(cabinet.id, user.id, "причина", admin.id, "admin")

    await db_session.refresh(users_chat)
    await db_session.refresh(others_chat)
    assert users_chat.archived_at is not None
    assert others_chat.archived_at is None


async def test_admin_unlink_notifies_user_without_leaking_admin_reason(db_session, make_user, make_cabinet, link_user_cabinet):
    admin = await make_user("admin")
    user = await make_user()
    cabinet = await make_cabinet(project_id=None, admin_internal_name="ШУ-Котельная")
    await link_user_cabinet(user, cabinet)

    await AdminUserService(db_session).remove_user_from_cabinet(cabinet.id, user.id, "внутренняя причина", admin.id, "admin")

    notes = await _notes(db_session, user.id, "Доступ к ШУ отозван")
    assert len(notes) == 1
    assert "ШУ-Котельная" in notes[0].body
    assert "внутренняя причина" not in notes[0].body


async def test_admin_unlink_writes_audit_log(db_session, make_user, make_cabinet, link_user_cabinet):
    admin = await make_user("admin")
    user = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(user, cabinet)

    await AdminUserService(db_session).remove_user_from_cabinet(cabinet.id, user.id, "причина", admin.id, "admin")

    entry = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "user_cabinet.remove", AuditLog.actor_id == admin.id)
    )).scalar_one_or_none()
    assert entry is not None
    assert entry.payload["cabinet_id"] == cabinet.id
    assert entry.payload["reason"] == "причина"


async def test_admin_cannot_unlink_cabinet_that_comes_through_project(db_session, make_user, make_project, make_cabinet, link_user_project):
    admin = await make_user("admin")
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(user, project)

    with pytest.raises(AlreadyExistsError):
        await AdminUserService(db_session).remove_user_from_cabinet(cabinet.id, user.id, "причина", admin.id, "admin")


async def test_admin_unlink_user_without_binding_is_not_found(db_session, make_user, make_cabinet):
    admin = await make_user("admin")
    user = await make_user()
    cabinet = await make_cabinet(project_id=None)

    with pytest.raises(NotFoundError):
        await AdminUserService(db_session).remove_user_from_cabinet(cabinet.id, user.id, "причина", admin.id, "admin")


async def test_admin_unlink_unknown_cabinet_is_not_found(db_session, make_user):
    admin = await make_user("admin")
    user = await make_user()

    with pytest.raises(NotFoundError):
        await AdminUserService(db_session).remove_user_from_cabinet(999999, user.id, "причина", admin.id, "admin")


# --- ProjectService.remove_user_from_project: уведомление ---

async def test_admin_removing_user_from_project_notifies_user(db_session, make_user, make_project, link_user_project):
    admin = await make_user("admin")
    user = await make_user()
    project = await make_project(name="Бизнес-центр Космос")
    await link_user_project(user, project)

    await ProjectService(db_session).remove_user_from_project(project.id, user.id, "причина", admin.id, "admin")

    member = (await db_session.execute(
        select(UserProject).where(UserProject.user_id == user.id, UserProject.project_id == project.id)
    )).scalar_one_or_none()
    assert member is None
    notes = await _notes(db_session, user.id, "Доступ к проекту отозван")
    assert len(notes) == 1
    assert "Бизнес-центр Космос" in notes[0].body
    assert "причина" not in notes[0].body


# --- карточка пользователя в админке ---

async def test_user_detail_lists_only_directly_added_cabinets(db_session, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet):
    user = await make_user()
    project = await make_project()
    await make_cabinet(project_id=project.id)  # шкаф проекта — отдельно не перечисляется
    direct = await make_cabinet(project_id=None, admin_internal_name="Мой отдельный")
    await link_user_project(user, project)
    await link_user_cabinet(user, direct)

    detail = await AdminUserService(db_session).get_user_detail(user.id)

    assert [c.cabinet_id for c in detail.cabinets] == [direct.id]
    assert detail.cabinets[0].admin_internal_name == "Мой отдельный"
    assert [p.project_id for p in detail.projects] == [project.id]


async def test_user_detail_without_direct_cabinets_has_empty_list(db_session, make_user):
    user = await make_user()

    detail = await AdminUserService(db_session).get_user_detail(user.id)

    assert detail.cabinets == []


async def test_direct_binding_is_removed_from_table(db_session, make_user, make_cabinet, link_user_cabinet):
    admin = await make_user("admin")
    user = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(user, cabinet)

    await AdminUserService(db_session).remove_user_from_cabinet(cabinet.id, user.id, "причина", admin.id, "admin")

    rows = (await db_session.execute(select(UserCabinet).where(UserCabinet.user_id == user.id))).scalars().all()
    assert rows == []
