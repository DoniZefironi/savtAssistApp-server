"""GET /admin/reclamations: поиск (search) и сортировка (sort_by/sort_order).
Поиск идёт по заявителю, описанию, кодам ошибок, заводскому номеру из
object_details, номерам договора/заказа/ТТН и названию проекта. Bitrix не
вызывается.
"""
from datetime import date, datetime, timedelta, timezone

from app.services.reclamation_service import ReclamationService


async def _ids(db_session, **kwargs):
    page = await ReclamationService(db_session).list_admin(
        kwargs.pop("status", None), kwargs.pop("object_type", None), kwargs.pop("warranty_classification", None),
        1, 50, **kwargs,
    )
    return [item.id for item in page.items], page.total


# --- поиск ---

async def test_search_by_description(db_session, make_reclamation):
    hit = await make_reclamation(description="Не работает частотный преобразователь")
    await make_reclamation(description="Сломалась кнопка")

    ids, total = await _ids(db_session, search="преобразователь")

    assert ids == [hit.id]
    assert total == 1


async def test_search_by_error_codes_and_document_numbers(db_session, make_reclamation):
    by_error = await make_reclamation(error_codes="E-4711")
    by_contract = await make_reclamation(contract_number="Д-2026/88")
    by_order = await make_reclamation(order_number="З-5150")
    by_ttn = await make_reclamation(ttn_number="ТТН-9001")
    await make_reclamation()

    assert (await _ids(db_session, search="E-4711"))[0] == [by_error.id]
    assert (await _ids(db_session, search="2026/88"))[0] == [by_contract.id]
    assert (await _ids(db_session, search="5150"))[0] == [by_order.id]
    assert (await _ids(db_session, search="9001"))[0] == [by_ttn.id]


async def test_search_by_serial_number_in_object_details(db_session, make_reclamation):
    hit = await make_reclamation(object_type="line", object_details={"serial_number": "AL-2026-014"})
    await make_reclamation(object_type="line", object_details={"serial_number": "ZZ-1"})

    ids, _ = await _ids(db_session, search="AL-2026-014")

    assert ids == [hit.id]


async def test_search_by_applicant_name_and_phone(db_session, make_user, make_reclamation):
    applicant = await make_user(full_name="Сидоров Семён Петрович", phone="+375291119999")
    other = await make_user(full_name="Иванов Иван", phone="+375290000001")
    hit = await make_reclamation(user=applicant)
    await make_reclamation(user=other)

    assert (await _ids(db_session, search="Сидоров"))[0] == [hit.id]
    assert (await _ids(db_session, search="1119999"))[0] == [hit.id]


async def test_search_by_project_name(db_session, make_project, make_reclamation):
    project = await make_project(name="Агрокомбинат Ждановичи")
    hit = await make_reclamation(project_id=project.id)
    await make_reclamation()

    ids, _ = await _ids(db_session, search="Ждановичи")

    assert ids == [hit.id]


async def test_search_with_no_matches_returns_empty(db_session, make_reclamation):
    await make_reclamation()

    ids, total = await _ids(db_session, search="такого-текста-нигде-нет-xyz")

    assert ids == []
    assert total == 0


async def test_search_combines_with_filters(db_session, make_reclamation):
    new_hit = await make_reclamation(description="Течёт насос", status="new")
    await make_reclamation(description="Течёт насос", status="resolved")

    ids, _ = await _ids(db_session, search="насос", status="new")

    assert ids == [new_hit.id]


async def test_search_total_counts_all_matches_not_only_page(db_session, make_reclamation):
    for _ in range(3):
        await make_reclamation(description="Одинаковая неисправность насоса")

    page = await ReclamationService(db_session).list_admin(None, None, None, 1, 2, search="насоса")

    assert len(page.items) == 2
    assert page.total == 3


# --- сортировка ---

async def test_default_sort_is_newest_first(db_session, make_reclamation):
    now = datetime.now(timezone.utc)
    old = await make_reclamation(created_at=now - timedelta(days=2))
    new = await make_reclamation(created_at=now)

    ids, _ = await _ids(db_session)

    assert ids.index(new.id) < ids.index(old.id)


async def test_sort_by_created_at_asc(db_session, make_reclamation):
    now = datetime.now(timezone.utc)
    old = await make_reclamation(created_at=now - timedelta(days=2))
    new = await make_reclamation(created_at=now)

    ids, _ = await _ids(db_session, sort_by="created_at", sort_order="asc")

    assert ids.index(old.id) < ids.index(new.id)


async def test_sort_by_deadline_puts_missing_deadlines_last(db_session, make_reclamation):
    today = date.today()
    soon = await make_reclamation(deadline_at=today + timedelta(days=1))
    later = await make_reclamation(deadline_at=today + timedelta(days=9))
    none = await make_reclamation(deadline_at=None)

    asc, _ = await _ids(db_session, sort_by="deadline_at", sort_order="asc")
    desc, _ = await _ids(db_session, sort_by="deadline_at", sort_order="desc")

    assert asc.index(soon.id) < asc.index(later.id) < asc.index(none.id)
    assert desc.index(later.id) < desc.index(soon.id) < desc.index(none.id)


async def test_sort_by_applicant_name(db_session, make_user, make_reclamation):
    a = await make_user(full_name="Аааа Первый")
    z = await make_user(full_name="Яяяя Последний")
    rec_z = await make_reclamation(user=z)
    rec_a = await make_reclamation(user=a)

    ids, _ = await _ids(db_session, sort_by="user_full_name", sort_order="asc")

    assert ids.index(rec_a.id) < ids.index(rec_z.id)


async def test_unknown_sort_key_falls_back_to_created_at(db_session, make_reclamation):
    await make_reclamation()

    ids, total = await _ids(db_session, sort_by="что-угодно")

    assert total == len(ids) >= 1
