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


async def test_number_query_does_not_match_neighbouring_number(db_session, make_cabinet, make_reclamation):
    # "26_204_1" и "26_205_1" почти совпадают по триграммам — но это разные ШУ
    near = await make_cabinet(object_number="26_205_1")
    target = await make_cabinet(object_number="26_204_1")
    await make_reclamation(cabinet_id=near.id)
    hit = await make_reclamation(cabinet_id=target.id)

    ids, _ = await _ids(db_session, search="26_204_1")

    assert ids == [hit.id]


async def test_number_query_still_matches_by_substring(db_session, make_cabinet, make_reclamation):
    cabinet = await make_cabinet(object_number="26_205_1")
    hit = await make_reclamation(cabinet_id=cabinet.id)

    assert (await _ids(db_session, search="26_205"))[0] == [hit.id]
    assert (await _ids(db_session, search="205_1"))[0] == [hit.id]


async def test_contract_number_query_is_exact_not_fuzzy(db_session, make_reclamation):
    await make_reclamation(contract_number="Д-2026/88")

    assert (await _ids(db_session, search="Д-2026/89"))[0] == []


async def test_search_by_object_type_label(db_session, make_user, make_reclamation):
    # имя без "по"/"шу"/"линия" — у стандартных тестовых пользователей оно
    # "Тестовый Пользователь", а "по" входит в "Пользователь"
    applicant = await make_user(full_name="Иванов Иван", phone="+375290001111")
    cabinet = await make_reclamation(user=applicant, object_type="cabinet", object_details={"serial_number": "SN-1"})
    line = await make_reclamation(user=applicant, object_type="line", object_details={"serial_number": "SN-2"})
    software = await make_reclamation(user=applicant, object_type="software")

    assert (await _ids(db_session, search="ШУ"))[0] == [cabinet.id]
    assert (await _ids(db_session, search="линия"))[0] == [line.id]
    assert (await _ids(db_session, search="ПО"))[0] == [software.id]


async def test_search_by_status_label(db_session, make_reclamation):
    resolved = await make_reclamation(status="resolved")
    await make_reclamation(status="new")

    assert (await _ids(db_session, search="закрыта"))[0] == [resolved.id]


async def test_one_letter_query_does_not_expand_to_labels(db_session, make_reclamation):
    await make_reclamation(object_type="cabinet", object_details={"serial_number": "SN-1"})

    # одна буква слишком шумная, чтобы по ней подмешивать все подписи типов и статусов
    assert (await _ids(db_session, search="ш"))[0] == []


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


# --- несколько слов в запросе ---

async def test_multi_word_query_combines_label_and_number(db_session, make_cabinet, make_user, make_reclamation):
    applicant = await make_user(full_name="Иванов Иван", phone="+375290001111")
    c26 = await make_cabinet(object_number="26_204_1")
    c29 = await make_cabinet(object_number="29_001")
    hit = await make_reclamation(user=applicant, cabinet_id=c26.id, object_type="cabinet",
                                 object_details={"serial_number": "SN-1"})
    await make_reclamation(user=applicant, cabinet_id=c29.id, object_type="cabinet",
                           object_details={"serial_number": "SN-2"})
    await make_reclamation(user=applicant, object_type="line", object_details={"serial_number": "SN-26"})

    assert (await _ids(db_session, search="ШУ 26"))[0] == [hit.id]


async def test_multi_word_query_needs_every_word(db_session, make_reclamation):
    await make_reclamation(description="Течёт насос")

    assert (await _ids(db_session, search="насос вентилятор"))[0] == []


async def test_search_by_card_title_as_shown_in_admin(db_session, make_cabinet, make_user, make_reclamation):
    # заголовок карточки в админке — подпись типа + номер ШУ: "ШУ 26_205_1"
    applicant = await make_user(full_name="BOBA BOBI BOBOV", phone="+375290001111")
    c205 = await make_cabinet(object_number="26_205_1")
    c204 = await make_cabinet(object_number="26_204_1")
    hit_a = await make_reclamation(user=applicant, cabinet_id=c205.id, object_type="cabinet",
                                   object_details={"serial_number": "SN-1"})
    hit_b = await make_reclamation(user=applicant, cabinet_id=c205.id, object_type="cabinet",
                                   object_details={"serial_number": "SN-2"})
    await make_reclamation(user=applicant, cabinet_id=c204.id, object_type="cabinet",
                           object_details={"serial_number": "SN-3"})

    assert set((await _ids(db_session, search="ШУ 26_205_1"))[0]) == {hit_a.id, hit_b.id}
    assert set((await _ids(db_session, search="шу 26_205"))[0]) == {hit_a.id, hit_b.id}
    assert len((await _ids(db_session, search="ШУ 26"))[0]) == 3
    assert (await _ids(db_session, search="BOBA BOBOV"))[0] != []
