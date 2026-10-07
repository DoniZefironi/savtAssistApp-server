"""Закрепление ШУ наверху личного списка: у каждого пользователя своё, работает
и для ШУ из проекта, и для добавленного напрямую."""
import pytest

from app.core.exceptions import NotFoundError
from app.services.user_cabinet_service import UserCabinetService


async def _ids(db_session, user):
    return [c.cabinet_id for c in await UserCabinetService(db_session).list_cabinets(user.id)]


async def test_pinned_cabinet_goes_first_and_unpin_restores_order(
    db_session, make_user, make_project, make_cabinet, link_user_project,
):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)
    old = await make_cabinet(project_id=project.id)
    new = await make_cabinet(project_id=project.id)
    service = UserCabinetService(db_session)
    default = await _ids(db_session, user)
    assert set(default) == {old.id, new.id}
    last = default[-1]

    await service.set_pinned(user.id, last, True)

    assert (await _ids(db_session, user))[0] == last
    items = await service.list_cabinets(user.id)
    assert [i.is_pinned for i in items] == [True, False]

    await service.set_pinned(user.id, last, False)

    assert await _ids(db_session, user) == default
    assert not any(i.is_pinned for i in await service.list_cabinets(user.id))


async def test_latest_pinned_is_above_earlier_pinned(
    db_session, make_user, make_project, make_cabinet, link_user_project,
):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)
    cabinets = [await make_cabinet(project_id=project.id) for _ in range(3)]
    service = UserCabinetService(db_session)

    await service.set_pinned(user.id, cabinets[0].id, True)
    await service.set_pinned(user.id, cabinets[1].id, True)

    ids = await _ids(db_session, user)
    assert ids[:2] == [cabinets[1].id, cabinets[0].id]
    assert ids[2] == cabinets[2].id


async def test_pin_works_for_directly_added_cabinet(db_session, make_user, make_cabinet, link_user_cabinet):
    user = await make_user()
    a = await make_cabinet()
    b = await make_cabinet()
    await link_user_cabinet(user, a)
    await link_user_cabinet(user, b)
    service = UserCabinetService(db_session)

    await service.set_pinned(user.id, a.id, True)
    await service.set_pinned(user.id, b.id, True)
    await service.set_pinned(user.id, a.id, False)

    assert (await _ids(db_session, user))[0] == b.id


async def test_pin_is_personal(db_session, make_user, make_project, make_cabinet, link_user_project):
    me = await make_user()
    other = await make_user()
    project = await make_project()
    await link_user_project(me, project)
    await link_user_project(other, project)
    cabinet = await make_cabinet(project_id=project.id)
    service = UserCabinetService(db_session)

    await service.set_pinned(me.id, cabinet.id, True)

    assert (await service.list_cabinets(me.id))[0].is_pinned is True
    assert (await service.list_cabinets(other.id))[0].is_pinned is False


async def test_cannot_pin_cabinet_without_access(db_session, make_user, make_cabinet):
    user = await make_user()
    cabinet = await make_cabinet()

    with pytest.raises(NotFoundError):
        await UserCabinetService(db_session).set_pinned(user.id, cabinet.id, True)


async def test_detail_shows_is_pinned(db_session, make_user, make_project, make_cabinet, link_user_project):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)
    cabinet = await make_cabinet(project_id=project.id)
    service = UserCabinetService(db_session)
    assert (await service.get_cabinet(user.id, cabinet.id)).is_pinned is False

    await service.set_pinned(user.id, cabinet.id, True)

    assert (await service.get_cabinet(user.id, cabinet.id)).is_pinned is True
