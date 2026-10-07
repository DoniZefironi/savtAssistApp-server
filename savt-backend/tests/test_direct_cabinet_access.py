"""Доступ к ШУ даёт любое из двух: членство в проекте ШУ (UserProject) ИЛИ
прямая привязка (UserCabinet, ШУ добавлен отдельно по своему QR). Плюс
find_active — членство в проекте с учётом того, что проект могли удалить.
Ничего внешнего не трогает, чистая БД.
"""
from datetime import datetime, timedelta, timezone

from app.repositories.cabinet import CabinetRepository, UserCabinetRepository
from app.repositories.project import UserProjectRepository


# --- user_has_access / get_accessible_for_user ---

async def test_direct_binding_gives_access_to_cabinet_without_project(db_session, make_user, make_cabinet, link_user_cabinet):
    user = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(user, cabinet)

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is True


async def test_direct_binding_works_even_if_cabinet_belongs_to_foreign_project(db_session, make_user, make_project, make_cabinet, link_user_cabinet):
    user = await make_user()
    foreign_project = await make_project()
    cabinet = await make_cabinet(project_id=foreign_project.id)
    await link_user_cabinet(user, cabinet)  # в самом проекте пользователь не состоит

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is True


async def test_direct_binding_of_another_user_gives_nothing(db_session, make_user, make_cabinet, link_user_cabinet):
    owner = await make_user()
    stranger = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(owner, cabinet)

    assert await CabinetRepository(db_session).user_has_access(stranger.id, cabinet.id) is False


async def test_soft_deleted_cabinet_has_no_access_even_with_direct_binding(db_session, make_user, make_cabinet, link_user_cabinet):
    user = await make_user()
    cabinet = await make_cabinet(deleted_at=datetime.now(timezone.utc))
    await link_user_cabinet(user, cabinet)

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is False


# --- list_accessible_for_user ---

async def test_list_accessible_includes_both_paths(db_session, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet):
    user = await make_user()
    project = await make_project()
    via_project = await make_cabinet(project_id=project.id)
    standalone = await make_cabinet(project_id=None)
    unrelated = await make_cabinet(project_id=None)
    await link_user_project(user, project)
    await link_user_cabinet(user, standalone)

    ids = {c.id for c in await CabinetRepository(db_session).list_accessible_for_user(user.id)}

    assert ids == {via_project.id, standalone.id}
    assert unrelated.id not in ids


async def test_list_accessible_has_no_duplicates_when_both_paths_reach_same_cabinet(db_session, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(user, project)
    await link_user_cabinet(user, cabinet)

    cabinets = await CabinetRepository(db_session).list_accessible_for_user(user.id)

    assert [c.id for c in cabinets] == [cabinet.id]


# --- list_users_with_access ---

async def test_list_users_with_access_unions_project_members_and_direct_owners(db_session, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet):
    member = await make_user()
    direct_owner = await make_user()
    outsider = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(member, project)
    await link_user_cabinet(direct_owner, cabinet)

    rows = await CabinetRepository(db_session).list_users_with_access(cabinet.id)

    assert {user.id for user, _ in rows} == {member.id, direct_owner.id}
    assert outsider.id not in {user.id for user, _ in rows}


async def test_list_users_with_access_lists_user_once_if_both_paths(db_session, make_user, make_project, make_cabinet, link_user_project, link_user_cabinet):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(user, project)
    await link_user_cabinet(user, cabinet)

    rows = await CabinetRepository(db_session).list_users_with_access(cabinet.id)

    assert [u.id for u, _ in rows] == [user.id]


async def test_list_users_with_access_works_for_cabinet_without_project(db_session, make_user, make_cabinet, link_user_cabinet):
    user = await make_user()
    cabinet = await make_cabinet(project_id=None)
    await link_user_cabinet(user, cabinet)

    rows = await CabinetRepository(db_session).list_users_with_access(cabinet.id)

    assert [u.id for u, _ in rows] == [user.id]


async def test_list_users_with_access_sorted_by_added_at(db_session, make_user, make_cabinet, link_user_cabinet):
    first = await make_user()
    second = await make_user()
    cabinet = await make_cabinet(project_id=None)
    now = datetime.now(timezone.utc)
    await link_user_cabinet(second, cabinet, added_at=now)
    await link_user_cabinet(first, cabinet, added_at=now - timedelta(days=1))

    rows = await CabinetRepository(db_session).list_users_with_access(cabinet.id)

    assert [u.id for u, _ in rows] == [first.id, second.id]


# --- UserCabinetRepository ---

async def test_list_with_cabinets_hides_soft_deleted(db_session, make_user, make_cabinet, link_user_cabinet):
    user = await make_user()
    alive = await make_cabinet()
    dead = await make_cabinet(deleted_at=datetime.now(timezone.utc))
    await link_user_cabinet(user, alive)
    await link_user_cabinet(user, dead)

    rows = await UserCabinetRepository(db_session).list_with_cabinets(user.id)

    assert [cab.id for _, cab in rows] == [alive.id]


async def test_list_for_user_in_cabinets_returns_only_requested(db_session, make_user, make_cabinet, link_user_cabinet):
    user = await make_user()
    a = await make_cabinet()
    b = await make_cabinet()
    await link_user_cabinet(user, a)
    await link_user_cabinet(user, b)

    rows = await UserCabinetRepository(db_session).list_for_user_in_cabinets(user.id, [a.id])

    assert [uc.cabinet_id for uc in rows] == [a.id]


async def test_list_for_user_in_cabinets_empty_ids(db_session, make_user):
    user = await make_user()
    assert await UserCabinetRepository(db_session).list_for_user_in_cabinets(user.id, []) == []


# --- UserProjectRepository.find_active ---

async def test_find_active_returns_membership_of_live_project(db_session, make_user, make_project, link_user_project):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)

    assert await UserProjectRepository(db_session).find_active(user.id, project.id) is not None


async def test_find_active_hides_soft_deleted_project(db_session, make_user, make_project, link_user_project):
    user = await make_user()
    project = await make_project(deleted_at=datetime.now(timezone.utc))
    await link_user_project(user, project)

    repo = UserProjectRepository(db_session)
    assert await repo.find_active(user.id, project.id) is None
    # find() намеренно не смотрит на удаление проекта — выйти из него должно быть можно
    assert await repo.find(user.id, project.id) is not None


async def test_find_active_none_for_non_member(db_session, make_user, make_project):
    user = await make_user()
    project = await make_project()

    assert await UserProjectRepository(db_session).find_active(user.id, project.id) is None
