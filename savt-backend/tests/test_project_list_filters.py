"""Фильтры и сортировка списка проектов (ProjectRepository.search): каждый фильтр
по отдельности отдаёт ровно свою выборку, вместе они складываются по И,
релевантность поиска стоит впереди сортировки, пустые значения — в конце."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.cabinet_photo import CabinetPhoto
from app.models.project_contact import ProjectContact
from app.models.service_request import ServiceRequest
from app.models.tag import Tag
from app.repositories.cabinet import CabinetTag
from app.repositories.project import ProjectFilters, ProjectRepository

NOW = datetime.now(timezone.utc)


async def _ids(db_session, query=None, sort_by="created_at", sort_order="desc", **filters):
    items, total = await ProjectRepository(db_session).search(
        query=query, filters=ProjectFilters(**filters), sort_by=sort_by, sort_order=sort_order, limit=100,
    )
    return [p.id for p in items], total


@pytest.fixture
async def marker(make_project):
    """Общая часть названия: тест видит только свои проекты, а не всю базу."""
    return "ФЛТР"


async def _project(make_project, marker, name, **kw):
    return await make_project(name=f"{marker} {name}", **kw)


async def test_year_comes_from_the_number_or_the_creation_date(db_session, make_project, marker):
    by_number = await _project(make_project, marker, "по номеру", production_number="25_100")
    other = await _project(make_project, marker, "другой год", production_number="26_100")

    ids, _ = await _ids(db_session, marker, year=2025)

    assert ids == [by_number.id] and other.id not in ids


async def test_company_and_shipment_filters(db_session, make_project, marker):
    shipped = await _project(make_project, marker, "отгружен", company_name="ООО Север",
                             shipment_actual_at=NOW - timedelta(days=10), shipment_planned_at=NOW - timedelta(days=20))
    waiting = await _project(make_project, marker, "ждёт", company_name="ЗАО Юг",
                             shipment_planned_at=NOW + timedelta(days=20))

    assert (await _ids(db_session, marker, company="Север"))[0] == [shipped.id]
    assert (await _ids(db_session, marker, shipped=True))[0] == [shipped.id]
    assert (await _ids(db_session, marker, shipped=False))[0] == [waiting.id]
    assert (await _ids(db_session, marker, shipment_planned_from=NOW))[0] == [waiting.id]
    assert (await _ids(db_session, marker, shipment_planned_before=NOW))[0] == [shipped.id]
    assert (await _ids(db_session, marker, shipment_actual_from=NOW - timedelta(days=11)))[0] == [shipped.id]
    assert (await _ids(db_session, marker, shipment_actual_before=NOW - timedelta(days=11)))[0] == []


async def test_related_data_flags(db_session, make_project, make_document, make_user, link_user_project, marker):
    full = await _project(make_project, marker, "полный")
    empty = await _project(make_project, marker, "пустой")
    await make_document(project_id=full.id)
    db_session.add_all([
        CabinetPhoto(project_id=full.id, url="/static/p.jpg"),
        ProjectContact(project_id=full.id, bitrix_contact_id="1", full_name="Иванов", phones=[], emails=[]),
    ])
    await link_user_project(await make_user(), full)
    await db_session.flush()

    for flag in ("has_project_documents", "has_project_photos", "has_project_users", "has_contacts"):
        assert (await _ids(db_session, marker, **{flag: True}))[0] == [full.id], flag
        assert (await _ids(db_session, marker, **{flag: False}))[0] == [empty.id], flag


@pytest.mark.parametrize("status,delta", [
    ("active", timedelta(days=100)),
    ("expiring_soon", timedelta(days=10)),
    ("expired", timedelta(days=-5)),
    ("none", None),
])
async def test_warranty_status_filter(db_session, make_project, marker, status, delta):
    cases = {
        "active": NOW + timedelta(days=100), "expiring_soon": NOW + timedelta(days=10),
        "expired": NOW - timedelta(days=5), "none": None,
    }
    projects = {key: await _project(make_project, marker, key, warranty_ends_at=value) for key, value in cases.items()}

    ids, _ = await _ids(db_session, marker, warranty_status=status)

    assert ids == [projects[status].id]


async def test_cabinet_filters_match_if_any_cabinet_fits(
    db_session, make_project, make_cabinet, make_document, marker,
):
    with_docs = await _project(make_project, marker, "с документами шкафа")
    without = await _project(make_project, marker, "без")
    cabinet = await make_cabinet(project_id=with_docs.id, warranty_ends_at=NOW + timedelta(days=100))
    await make_cabinet(project_id=with_docs.id)
    await make_cabinet(project_id=without.id)
    await make_document(cabinet_id=cabinet.id)
    tag = Tag(name="важный шкаф", scope="cabinet")
    db_session.add(tag)
    await db_session.flush()
    db_session.add(CabinetTag(cabinet_id=cabinet.id, tag_id=tag.id))
    await db_session.flush()

    assert (await _ids(db_session, marker, has_documents=True))[0] == [with_docs.id]
    assert (await _ids(db_session, marker, tag_ids=[tag.id]))[0] == [with_docs.id]
    assert (await _ids(db_session, marker, cabinet_warranty_status="active"))[0] == [with_docs.id]
    assert (await _ids(db_session, marker, has_documents=True, cabinet_warranty_status="expired"))[0] == []


async def test_filters_combine_with_and_and_deleted_are_hidden(db_session, make_project, marker):
    both = await _project(make_project, marker, "оба", company_name="ООО Север", production_number="26_100")
    await _project(make_project, marker, "только компания", company_name="ООО Север", production_number="25_100")
    await _project(make_project, marker, "удалён", company_name="ООО Север", production_number="26_101",
                   deleted_at=NOW)

    ids, total = await _ids(db_session, marker, company="Север", year=2026)

    assert ids == [both.id] and total == 1


@pytest.mark.parametrize("sort_by", [
    "name", "created_at", "production_number", "year", "company_name",
    "shipment_planned_at", "shipment_actual_at", "warranty_ends_at", "cabinet_count", "unknown",
])
async def test_every_sort_key_works_in_both_directions(db_session, make_project, marker, sort_by):
    await _project(make_project, marker, "а", production_number="26_100")
    await _project(make_project, marker, "б", production_number="26_200")

    for order in ("asc", "desc"):
        ids, total = await _ids(db_session, marker, sort_by=sort_by, sort_order=order)
        assert total == 2 and len(ids) == 2


async def test_empty_values_are_last_in_both_directions(db_session, make_project, marker):
    early = await _project(make_project, marker, "рано", shipment_planned_at=NOW + timedelta(days=1))
    late = await _project(make_project, marker, "поздно", shipment_planned_at=NOW + timedelta(days=9))
    none = await _project(make_project, marker, "без даты")

    asc = (await _ids(db_session, marker, sort_by="shipment_planned_at", sort_order="asc"))[0]
    desc = (await _ids(db_session, marker, sort_by="shipment_planned_at", sort_order="desc"))[0]

    assert asc == [early.id, late.id, none.id] and desc == [late.id, early.id, none.id]


async def test_cabinet_count_sort_and_pagination(db_session, make_project, make_cabinet, marker):
    many = await _project(make_project, marker, "много")
    few = await _project(make_project, marker, "мало")
    for _ in range(3):
        await make_cabinet(project_id=many.id)
    await make_cabinet(project_id=few.id)

    ids, _ = await _ids(db_session, marker, sort_by="cabinet_count", sort_order="desc")
    page, total = await ProjectRepository(db_session).search(
        query=marker, sort_by="cabinet_count", sort_order="desc", offset=1, limit=1,
    )

    assert ids == [many.id, few.id] and [p.id for p in page] == [few.id] and total == 2


async def test_relevance_beats_the_chosen_sort(db_session, make_project):
    exact = await make_project(name="Сосны", production_number="26_999", created_at=NOW - timedelta(days=100))
    contact_only = await make_project(name="Другой", production_number="26_998")
    db_session.add(ProjectContact(project_id=contact_only.id, bitrix_contact_id="7", full_name="Сосновский",
                                  phones=[], emails=[]))
    await db_session.flush()

    newest_first, _ = await _ids(db_session, "Сосны", sort_by="created_at", sort_order="desc")

    assert newest_first[0] == exact.id  # точное название выше, хотя проект старше
    assert contact_only.id in newest_first
