"""Рекламации: подача заявителем и отправка в Bitrix (фоном, со сбоями и очередью
повторов), обратная синхронизация из Bitrix (статус, срок, ответственный,
гарантия), уведомления заявителю, отвязка удалённой карточки. Bitrix не
вызывается: пишущие функции записываются (mock_bitrix), читающие подменяются
в самих тестах. Фоновые задачи выполняются явно через env.run_background()."""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.exceptions import NotFoundError, ValidationError
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.reclamation import Reclamation
from app.models.reclamation_attachment import ReclamationAttachment
from app.models.reclamation_bitrix_outbox import ReclamationBitrixOutbox
from app.repositories.reclamation_outbox import ReclamationOutboxRepository
from app.schemas.reclamation import ReclamationCreateIn
from app.services import bitrix_service, reclamation_service
from app.services.reclamation_service import ReclamationService

DEADLINE_FIELD = "ufCrm53_1784791589794"
STAGE = bitrix_service._RECLAMATION_STATUS_TO_STAGE


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def env(db_session, monkeypatch, mock_bitrix):
    e = SimpleNamespace(
        spawned=[], bitrix_calls=mock_bitrix, item=None, item_error=None, warranty=None, warranty_error=None,
        assignee=None, fetched=[],
    )

    def spawn(coro, **kwargs):
        e.spawned.append(coro)

    async def run_background():
        pending, e.spawned = e.spawned, []
        for coro in pending:
            await coro

    async def get_reclamation_item(item_id):
        e.fetched.append(item_id)
        if e.item_error:
            raise e.item_error
        return e.item

    async def parse_warranty(item):
        if e.warranty_error:
            raise e.warranty_error
        return e.warranty

    async def get_bitrix_user(user_id):
        return e.assignee

    e.run_background = run_background
    monkeypatch.setattr(reclamation_service, "spawn", spawn)
    monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: _SessionContext(db_session))
    monkeypatch.setattr(bitrix_service, "get_reclamation_item", get_reclamation_item)
    monkeypatch.setattr(bitrix_service, "parse_reclamation_warranty", parse_warranty)
    monkeypatch.setattr(bitrix_service, "get_bitrix_user", get_bitrix_user)
    yield e
    for coro in e.spawned:
        coro.close()


def form(**kw):
    data = dict(
        object_type="cabinet", object_details={"serial_number": "SN-26-001"},
        description="Не работает кнопка управления", contact_name="Иванов Иван",
        contact_phone="+375291234567", contact_email="ivan@example.by",
    )
    data.update(kw)
    return ReclamationCreateIn(**data)


async def _notes(db_session, user_id, type_="request_status"):
    return list((await db_session.execute(
        select(Notification).where(Notification.user_id == user_id, Notification.type == type_)
        .order_by(Notification.id)
    )).scalars().all())


# --- подача: проверки ---

@pytest.mark.parametrize("object_type", ["cabinet", "line"])
@pytest.mark.parametrize("details", [None, {}, {"serial_number": ""}])
async def test_cabinet_and_line_need_a_serial_number(db_session, make_user, env, object_type, details):
    user = await make_user()

    with pytest.raises(ValidationError, match="заводской номер"):
        await ReclamationService(db_session).create(user.id, form(object_type=object_type, object_details=details))

    assert env.spawned == []


async def test_component_needs_all_four_details_and_names_the_missing_ones(db_session, make_user, env):
    user = await make_user()

    with pytest.raises(ValidationError) as error:
        await ReclamationService(db_session).create(user.id, form(
            object_type="component", object_details={"name": "Контактор", "model": "LC1D18"},
        ))

    assert "артикул" in str(error.value) and "серийный номер" in str(error.value)
    assert "наименование" not in str(error.value) and "модель" not in str(error.value)


@pytest.mark.parametrize("object_type", ["software", "documentation"])
async def test_software_and_documentation_need_no_details(db_session, make_user, env, object_type):
    user = await make_user()

    out = await ReclamationService(db_session).create(user.id, form(object_type=object_type, object_details=None))

    assert out.object_type == object_type and out.status == "new"


# --- подача: результат ---

async def test_create_stores_the_claim_and_sends_it_to_bitrix_in_the_background(db_session, make_user, env):
    user = await make_user()
    data = form(
        object_details={"serial_number": "SN-26-001"}, contract_number="Д-45/2026", order_number="З-102",
        ttn_number="ТТН-778", error_codes="E-12", occurrence_conditions="После простоя",
        customer_name="ООО Ромашка",
        attachments=[{"file_url": "/static/a.jpg", "file_name": "a.jpg", "file_size_bytes": 10, "mime_type": "image/jpeg"}],
    )

    out = await ReclamationService(db_session).create(user.id, data)

    rec = await db_session.get(Reclamation, out.id)
    assert (rec.user_id, rec.status, rec.object_type) == (user.id, "new", "cabinet")
    assert rec.object_details == {"serial_number": "SN-26-001"} and rec.error_codes == "E-12"
    assert rec.cabinet_id is None and rec.project_id is None  # ни к чему в системе не привязана
    attachments = (await db_session.execute(select(ReclamationAttachment).where(ReclamationAttachment.reclamation_id == rec.id))).scalars().all()
    assert [a.file_name for a in attachments] == ["a.jpg"]
    assert [a.file_name for a in out.attachments] == ["a.jpg"]
    [audit] = (await db_session.execute(select(AuditLog).where(AuditLog.action == "reclamation.create"))).scalars().all()
    assert (audit.entity_id, audit.actor_id, audit.actor_role) == (rec.id, user.id, "user")
    # в Bitrix ничего не ушло, пока не отработала фоновая задача
    assert env.bitrix_calls == []

    await env.run_background()

    [(name, args, _)] = env.bitrix_calls
    assert name == "create_reclamation_item"
    description, deal_id, company_id, _attachment, project_name, serial, contract_info, component_info = args
    assert "Не работает кнопка управления" in description and "Контакт: Иванов Иван" in description
    assert f"reclamation_id={rec.id}" in description
    assert (deal_id, company_id, project_name) == (None, None, None)
    assert serial == "SN-26-001" and contract_info == "Договор: Д-45/2026, Заказ: З-102, ТТН/CMR: ТТН-778"
    assert component_info is None
    assert rec.bitrix_item_id == "999999"


async def test_bitrix_not_configured_leaves_the_claim_without_a_card_and_without_noise(db_session, make_user, env, monkeypatch):
    async def create_item(*args, **kwargs):
        return None  # Bitrix не настроен

    monkeypatch.setattr(bitrix_service, "create_reclamation_item", create_item)
    user = await make_user()
    out = await ReclamationService(db_session).create(user.id, form())

    await env.run_background()

    assert (await db_session.get(Reclamation, out.id)).bitrix_item_id is None
    assert (await db_session.execute(select(ReclamationBitrixOutbox))).scalars().all() == []


# --- сбой отправки ---

async def test_failed_send_goes_to_the_retry_queue_and_admins_are_told(db_session, make_user, env, monkeypatch):
    async def create_item(*args, **kwargs):
        raise RuntimeError("Bitrix 502: Bad Gateway")

    monkeypatch.setattr(bitrix_service, "create_reclamation_item", create_item)
    applicant = await make_user()
    admin, superadmin, operator = await make_user("admin"), await make_user("superadmin"), await make_user("operator")
    inactive_admin = await make_user("admin", is_active=False)
    out = await ReclamationService(db_session).create(applicant.id, form())

    await env.run_background()

    [row] = (await db_session.execute(select(ReclamationBitrixOutbox))).scalars().all()
    assert (row.reclamation_id, row.operation) == (out.id, "create")
    assert "502" in row.last_error and row.payload["object_serial_number"] == "SN-26-001"
    assert "Контакт: Иванов Иван" in row.payload["description"]
    for staff in (admin, superadmin):
        [note] = await _notes(db_session, staff.id, "bitrix_sync_error")
        assert str(out.id) in note.body and "502" in note.body
    for outsider in (operator, applicant, inactive_admin):
        assert await _notes(db_session, outsider.id, "bitrix_sync_error") == []
    assert (await db_session.get(Reclamation, out.id)).bitrix_item_id is None


async def test_failure_with_a_deleted_card_detaches_instead_of_queueing(db_session, make_user, make_reclamation, env):
    admin = await make_user("admin")
    rec = await make_reclamation(bitrix_item_id="500")

    await reclamation_service._record_bitrix_failure(rec.id, "status", {"status": "resolved"}, RuntimeError("NOT_FOUND"))

    assert rec.bitrix_item_id is None and rec.bitrix_deleted_at is not None
    assert (await db_session.execute(select(ReclamationBitrixOutbox))).scalars().all() == []
    [note] = await _notes(db_session, admin.id, "bitrix_sync_error")
    assert "удалена" in note.title.lower() or "удалена" in note.body.lower()


# --- чтение заявителем ---

async def test_user_sees_only_their_own_claims(db_session, make_user, make_reclamation, env):
    me, other = await make_user(), await make_user()
    mine = await make_reclamation(user=me)
    await make_reclamation(user=other)
    service = ReclamationService(db_session)

    assert (await service.get_for_user(me.id, mine.id)).id == mine.id
    with pytest.raises(NotFoundError):
        await service.get_for_user(other.id, mine.id)
    page = await service.list_for_user(me.id, None, 1, 20)
    assert [i.id for i in page.items] == [mine.id] and page.total == 1


async def test_list_filters_by_status_and_paginates(db_session, make_user, make_reclamation, env):
    me = await make_user()
    for status in ("new", "new", "resolved"):
        await make_reclamation(user=me, status=status)
    service = ReclamationService(db_session)

    assert (await service.list_for_user(me.id, "resolved", 1, 20)).total == 1
    page = await service.list_for_user(me.id, None, 1, 2)
    assert len(page.items) == 2 and page.total == 3 and page.pages == 2


# --- уведомления заявителю о смене статуса ---

async def _status_note(db_session, make_user, make_reclamation, **fields):
    user = await make_user()
    rec = await make_reclamation(user=user, **fields)
    await ReclamationService(db_session)._notify_status_change(rec)
    notes = await _notes(db_session, user.id)
    return [n.body for n in notes], notes


@pytest.mark.parametrize("warranty,label", [
    (None, "В работе"), (True, "В работе. Гарантия"), (False, "В работе. Не гарантия"),
])
async def test_in_progress_text_depends_on_the_warranty_decision(db_session, make_user, make_reclamation, warranty, label):
    [body], _ = await _status_note(db_session, make_user, make_reclamation, status="in_progress", warranty_classification=warranty)

    assert body == f"Статус изменён: «{label}»"


async def test_in_progress_names_the_responsible_person(db_session, make_user, make_reclamation):
    [body], [note] = await _status_note(
        db_session, make_user, make_reclamation, status="in_progress",
        responsible_name="Грибовский В.", responsible_phone="+375445689338",
    )

    assert body.endswith("Ответственный: Грибовский В., тел. +375445689338")
    assert note.title == "Рекламация" and note.data["status"] == "in_progress"


@pytest.mark.parametrize("status,expected", [
    ("review", "Рекламация принята на рассмотрение"),
    ("rejected", "Рекламация отклонена"),
    ("invalid", "Рекламация оформлена некорректно"),
    ("resolved", "Рекламация исполнена"),
])
async def test_status_texts_never_print_a_missing_reason_as_none(db_session, make_user, make_reclamation, status, expected):
    """Причина/итог приходят только из Bitrix и обычно пусты — заявитель не должен
    увидеть «Причина: None»."""
    [body], _ = await _status_note(db_session, make_user, make_reclamation, status=status)

    assert body.startswith(expected) and "None" not in body


@pytest.mark.parametrize("status,field,expected", [
    ("rejected", "rejection_reason", "Рекламация отклонена. Причина: Не наш случай"),
    ("invalid", "rejection_reason", "Рекламация оформлена некорректно. Причина: Нет номера"),
    ("resolved", "resolution_comment", "Рекламация исполнена. Заменили плату"),
])
async def test_status_texts_include_the_reason_when_there_is_one(db_session, make_user, make_reclamation, status, field, expected):
    text = {"rejection_reason": "Не наш случай" if status == "rejected" else "Нет номера", "resolution_comment": "Заменили плату"}[field]
    [body], _ = await _status_note(db_session, make_user, make_reclamation, status=status, **{field: text})

    assert body == expected


async def test_new_status_is_silent(db_session, make_user, make_reclamation):
    bodies, _ = await _status_note(db_session, make_user, make_reclamation, status="new")

    assert bodies == []


# --- обратная синхронизация из Bitrix ---

def item(status="in_progress", assignee=None, deadline=None, stage=None):
    data = {"stageId": stage or STAGE[status], "assignedById": assignee}
    if deadline:
        data[DEADLINE_FIELD] = deadline
    return data


async def _sync(env, item_id="500"):
    await reclamation_service.sync_reclamation_from_bitrix(item_id)


async def test_unknown_card_is_ignored(db_session, make_reclamation, env):
    env.item = item()

    await _sync(env, "no-such-card")

    assert env.fetched == []  # даже не запрашиваем


@pytest.mark.parametrize("failure", ["error", "empty"])
async def test_unreadable_card_changes_nothing(db_session, make_reclamation, env, failure):
    rec = await make_reclamation(bitrix_item_id="500", status="new")
    if failure == "error":
        env.item_error = RuntimeError("сеть")
    else:
        env.item = None

    await _sync(env)

    assert rec.status == "new"


async def test_new_stage_updates_status_and_tells_the_applicant_once(db_session, make_user, make_reclamation, env):
    user = await make_user()
    rec = await make_reclamation(user=user, bitrix_item_id="500", status="new")
    env.item = item("in_progress")

    await _sync(env)
    await _sync(env)  # повторный вебхук с тем же статусом

    assert rec.status == "in_progress" and rec.resolved_at is None
    assert len(await _notes(db_session, user.id)) == 1


@pytest.mark.parametrize("status", ["resolved", "rejected", "invalid"])
async def test_final_statuses_set_resolved_at(db_session, make_reclamation, env, status):
    rec = await make_reclamation(bitrix_item_id="500", status="in_progress")
    env.item = item(status)

    await _sync(env)

    assert rec.status == status and rec.resolved_at is not None


async def test_resolved_at_is_kept_when_status_changes_between_final_ones(db_session, make_reclamation, env):
    closed_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rec = await make_reclamation(bitrix_item_id="500", status="resolved", resolved_at=closed_at)
    env.item = item("invalid")

    await _sync(env)

    assert rec.status == "invalid" and rec.resolved_at == closed_at


async def test_reopened_claim_loses_its_closing_date(db_session, make_reclamation, env):
    """Закрытую рекламацию вернули в работу — даты закрытия у неё больше нет,
    иначе в админке у незакрытой стоит «закрыта такого-то числа»."""
    rec = await make_reclamation(
        bitrix_item_id="500", status="resolved", resolved_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    env.item = item("in_progress")

    await _sync(env)

    assert rec.status == "in_progress" and rec.resolved_at is None


async def test_unknown_stage_keeps_the_status_but_takes_the_deadline(db_session, make_reclamation, env):
    rec = await make_reclamation(bitrix_item_id="500", status="review")
    env.item = item(stage="DT1176_69:НЕЗНАКОМАЯ", deadline="2026-10-20T03:00:00+03:00")

    await _sync(env)

    assert rec.status == "review" and rec.deadline_at == date(2026, 10, 20)


async def test_deadline_changes_come_back_even_without_a_status_change(db_session, make_user, make_reclamation, env):
    user = await make_user()
    rec = await make_reclamation(user=user, bitrix_item_id="500", status="review", deadline_at=date(2026, 10, 1))
    env.item = item("review", deadline="2026-10-25")

    await _sync(env)

    assert rec.deadline_at == date(2026, 10, 25)
    assert await _notes(db_session, user.id) == []


async def test_assignee_is_resolved_to_name_and_phone(db_session, make_reclamation, env):
    rec = await make_reclamation(bitrix_item_id="500", status="review")
    env.item = item("review", assignee="7")
    env.assignee = {"id": 7, "full_name": "Грибовский Виталий", "phone": "+375445689338"}

    await _sync(env)

    assert rec.responsible_bitrix_user_id == 7
    assert (rec.responsible_name, rec.responsible_phone) == ("Грибовский Виталий", "+375445689338")


async def test_unresolvable_assignee_keeps_the_known_contact(db_session, make_reclamation, env):
    rec = await make_reclamation(
        bitrix_item_id="500", status="review", responsible_bitrix_user_id=3,
        responsible_name="Старый Ответственный", responsible_phone="+375290000000",
    )
    env.item = item("review", assignee="7")
    env.assignee = None  # Bitrix недоступен

    await _sync(env)

    assert rec.responsible_bitrix_user_id == 7
    assert rec.responsible_name == "Старый Ответственный"


async def test_unassigned_card_clears_the_contact(db_session, make_reclamation, env):
    rec = await make_reclamation(
        bitrix_item_id="500", status="review", responsible_bitrix_user_id=3,
        responsible_name="Старый", responsible_phone="+375290000000",
    )
    env.item = item("review", assignee=None)

    await _sync(env)

    assert rec.responsible_bitrix_user_id is None
    assert (rec.responsible_name, rec.responsible_phone) == (None, None)


@pytest.mark.parametrize("answer", [True, False, None])
async def test_warranty_decision_comes_back(db_session, make_reclamation, env, answer):
    rec = await make_reclamation(bitrix_item_id="500", status="review", warranty_classification=(not answer) if answer is not None else True)
    env.item = item("review")
    env.warranty = answer

    await _sync(env)

    assert rec.warranty_classification is answer


async def test_failed_warranty_lookup_does_not_erase_it_and_status_still_applies(db_session, make_reclamation, env):
    rec = await make_reclamation(bitrix_item_id="500", status="new", warranty_classification=True)
    env.item = item("in_progress")
    env.warranty_error = RuntimeError("не удалось прочитать варианты")

    await _sync(env)

    assert rec.warranty_classification is True and rec.status == "in_progress"


async def test_pending_status_push_blocks_the_status_but_not_the_other_fields(db_session, make_reclamation, env):
    """Наш неотправленный статус новее карточки: вебхук не должен откатывать его,
    при этом срок из карточки берётся."""
    rec = await make_reclamation(bitrix_item_id="500", status="resolved")
    await ReclamationOutboxRepository(db_session).create(rec.id, "status", {"status": "resolved"}, "502")
    env.item = item("review", deadline="2026-10-25")

    await _sync(env)

    assert rec.status == "resolved" and rec.deadline_at == date(2026, 10, 25)


# --- удалённая карточка ---

async def test_deleted_card_event_detaches_the_claim_and_clears_its_queue(db_session, make_user, make_reclamation, env):
    admin = await make_user("admin")
    rec = await make_reclamation(bitrix_item_id="500")
    await ReclamationOutboxRepository(db_session).create(rec.id, "comment", {"text": "x"}, "502")

    await reclamation_service.handle_bitrix_item_deleted("500")

    assert rec.bitrix_item_id is None and rec.bitrix_deleted_at is not None
    assert (await db_session.execute(select(ReclamationBitrixOutbox))).scalars().all() == []
    assert len(await _notes(db_session, admin.id, "bitrix_sync_error")) == 1


async def test_deleted_card_event_for_an_unknown_card_does_nothing(db_session, make_user, env):
    admin = await make_user("admin")

    await reclamation_service.handle_bitrix_item_deleted("no-such")

    assert await _notes(db_session, admin.id, "bitrix_sync_error") == []


async def test_only_detached_claims_can_be_deleted(db_session, make_user, make_reclamation, env):
    admin = await make_user("admin")
    live = await make_reclamation(bitrix_item_id="500")
    detached = await make_reclamation(bitrix_deleted_at=datetime.now(timezone.utc))
    service = ReclamationService(db_session)

    with pytest.raises(ValidationError):
        await service.delete_detached(live.id, admin.id, "admin")
    with pytest.raises(NotFoundError):
        await service.delete_detached(987654, admin.id, "admin")
    assert [d.id for d in await service.list_detached()] == [detached.id]

    await service.delete_detached(detached.id, admin.id, "admin")

    assert await db_session.get(Reclamation, detached.id) is None
    assert await db_session.get(Reclamation, live.id) is not None
    [audit] = (await db_session.execute(select(AuditLog).where(AuditLog.action == "reclamation.delete"))).scalars().all()
    assert audit.entity_id == detached.id and audit.actor_id == admin.id


# --- очередь: ручное вмешательство ---

async def test_manual_retry_fixes_the_payload_and_sends_at_once(db_session, make_reclamation, env):
    rec = await make_reclamation()
    row = await ReclamationOutboxRepository(db_session).create(rec.id, "create", {"description": "старое"}, "company_id отсутствует")

    result = await ReclamationService(db_session).retry_outbox_now(row.id, {"description": "исправленное", "company_id": 77})

    assert result.success is True and rec.bitrix_item_id == "999999"
    name, args, _ = env.bitrix_calls[-1]
    assert name == "create_reclamation_item" and args[0] == "исправленное" and args[2] == 77
    assert (await db_session.execute(select(ReclamationBitrixOutbox))).scalars().all() == []


async def test_manual_retry_of_a_missing_row_and_delete_of_the_queue_item(db_session, make_reclamation, env):
    service = ReclamationService(db_session)
    assert await service.retry_outbox_now(987654, {}) is None
    assert await service.delete_outbox(987654) is False

    rec = await make_reclamation()
    row = await ReclamationOutboxRepository(db_session).create(rec.id, "create", {"description": "x"}, "err")
    assert [r.id for r in await service.list_outbox()] == [row.id]

    assert await service.delete_outbox(row.id) is True
    assert await service.list_outbox() == []
