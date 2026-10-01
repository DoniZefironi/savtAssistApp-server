"""Доступ к ШУ и проектам выводится из членства в проекте (UserProject), не
хранится по-шкафно — ядро всей модели прав в приложении. Ничего внешнего не
трогает, чистая БД.
"""
from datetime import datetime, timezone

from app.repositories.cabinet import CabinetRepository
from app.repositories.project import UserProjectRepository


# --- CabinetRepository.user_has_access ---

async def test_member_of_cabinet_project_has_access(db_session, make_user, make_project, make_cabinet, link_user_project):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(user, project)

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is True


async def test_non_member_has_no_access(db_session, make_user, make_project, make_cabinet):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    # пользователь ни в каком проекте не состоит вообще

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is False


async def test_member_of_different_project_has_no_access(db_session, make_user, make_project, make_cabinet, link_user_project):
    user = await make_user()
    own_project = await make_project()
    other_project = await make_project()
    cabinet = await make_cabinet(project_id=other_project.id)
    await link_user_project(user, own_project)  # состоит в СВОЁМ проекте, не в том, что владеет ШУ

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is False


async def test_cabinet_without_project_has_no_access_for_anyone(db_session, make_user, make_project, make_cabinet, link_user_project):
    # "У ШУ без project_id доступа нет ни у кого, кроме админа/оператора" (см. комментарий в коде)
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=None)
    await link_user_project(user, project)  # состоит хоть в каком-то проекте — не помогает

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is False


async def test_soft_deleted_cabinet_has_no_access_even_for_member(db_session, make_user, make_project, make_cabinet, link_user_project):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id, deleted_at=datetime.now(timezone.utc))
    await link_user_project(user, project)

    assert await CabinetRepository(db_session).user_has_access(user.id, cabinet.id) is False


async def test_list_accessible_for_user_excludes_soft_deleted(db_session, make_user, make_project, make_cabinet, link_user_project):
    user = await make_user()
    project = await make_project()
    live = await make_cabinet(project_id=project.id)
    await make_cabinet(project_id=project.id, deleted_at=datetime.now(timezone.utc))
    await link_user_project(user, project)

    cabinets = await CabinetRepository(db_session).list_accessible_for_user(user.id)
    assert [c.id for c in cabinets] == [live.id]


# --- UserProjectRepository.find — проверка членства, используется для доступа
# к созданию рекламаций/заявок/чата/документов (4 места в коде, не только ШУ) ---

async def test_find_returns_membership_row(db_session, make_user, make_project, link_user_project):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)

    assert await UserProjectRepository(db_session).find(user.id, project.id) is not None


async def test_find_none_when_not_a_member(db_session, make_user, make_project):
    user = await make_user()
    project = await make_project()

    assert await UserProjectRepository(db_session).find(user.id, project.id) is None


async def test_find_still_returns_membership_for_soft_deleted_project(db_session, make_user, make_project, link_user_project):
    """ВНИМАНИЕ: это не то же самое поведение, что у list_for_user/get_with_project
    (оба фильтруют Project.deleted_at) — find() его не фильтрует вообще. Значит
    пока проект в мягко удалённом состоянии, бывший участник не увидит его в
    своём списке (list_for_user), но всё ещё пройдёт проверку доступа на
    создание рекламации/заявки/чата/документа (reclamation_service.py:56,
    service_request_service.py:336, chat_service.py:89, document_service.py:358
    — все вызывают именно find(), не get_with_project()). Тест фиксирует
    СУЩЕСТВУЮЩЕЕ поведение, а не то, что оно обязательно правильное — возможно,
    стоит обсудить с продуктом, баг это или осознанная асимметрия."""
    user = await make_user()
    project = await make_project(deleted_at=datetime.now(timezone.utc))
    await link_user_project(user, project)

    assert await UserProjectRepository(db_session).find(user.id, project.id) is not None


async def test_list_for_user_excludes_soft_deleted_project(db_session, make_user, make_project, link_user_project):
    user = await make_user()
    live = await make_project()
    deleted = await make_project(deleted_at=datetime.now(timezone.utc))
    await link_user_project(user, live)
    await link_user_project(user, deleted)

    rows = await UserProjectRepository(db_session).list_for_user(user.id)
    assert [project.id for _up, project in rows] == [live.id]
