"""Заведение и обновление проектов по сделкам Bitrix (вебхук ONCRMDEALADD/UPDATE и
разовый импорт). Bitrix не вызывается: компания и контакты подменяются, запуск
папок на NAS — тоже, проверяется только что он был запрошен."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.models.project import Project
from app.repositories.project import ProjectContactRepository
from app.services import bitrix_service, bitrix_webhook_service as hooks, project_code_service, project_folder_service


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def bitrix(monkeypatch):
    """Подмена всего внешнего: ключ шифрования, компания, контакты, папки на NAS."""
    fake = SimpleNamespace(
        companies={}, deal_contacts={}, contacts={}, created_folders=[], synced_folders=[],
    )
    monkeypatch.setattr(settings, "bitrix_project_code_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "bitrix_field_production_number", "")
    monkeypatch.setattr(settings, "bitrix_production_years", "")
    monkeypatch.setattr(settings, "bitrix_field_shipment_planned", "UF_PLAN")
    monkeypatch.setattr(settings, "bitrix_field_shipment_actual", "UF_NEW_FACT,UF_OLD_FACT")

    async def get_company_name(company_id):
        return fake.companies.get(company_id)

    async def get_deal_contact_ids(deal_id):
        return fake.deal_contacts.get(deal_id, [])

    async def get_contact(contact_id):
        data = fake.contacts.get(contact_id)
        return dict(data) if data else None

    monkeypatch.setattr(bitrix_service, "get_company_name", get_company_name)
    monkeypatch.setattr(bitrix_service, "get_deal_contact_ids", get_deal_contact_ids)
    monkeypatch.setattr(bitrix_service, "get_contact", get_contact)
    monkeypatch.setattr(project_folder_service, "schedule_folder_creation", fake.created_folders.append)
    monkeypatch.setattr(project_folder_service, "schedule_folder_sync", fake.synced_folders.append)
    return fake


def deal(deal_id="501", title="26_138 ЖК Сосны, ул. Ленина 5", **extra):
    return {"ID": str(deal_id), "TITLE": title, **extra}


async def _projects(db_session, production_number):
    result = await db_session.execute(select(Project).where(Project.production_number == production_number))
    return list(result.scalars().all())


# --- номер проекта из сделки ---

@pytest.mark.parametrize("title,expected", [
    ("26_138 ЖК Сосны", "26_138"),
    ("26_138", "26_138"),
    ("25_007-А Завод", "25_007-А"),
    ("ШУ 26_138", None),
    ("Без номера", None),
    ("2026_138 длинный год", None),
    ("", None),
])
def test_production_number_from_title(title, expected, bitrix):
    assert hooks.extract_production_number({"TITLE": title}) == expected


def test_production_number_prefers_dedicated_field_and_falls_back_to_title(monkeypatch, bitrix):
    monkeypatch.setattr(settings, "bitrix_field_production_number", "UF_NUMBER")

    assert hooks.extract_production_number({"TITLE": "26_001 Название", "UF_NUMBER": "26_777"}) == "26_777"
    assert hooks.extract_production_number({"TITLE": "26_001 Название", "UF_NUMBER": ""}) == "26_001"
    assert hooks.extract_production_number({"TITLE": "Опечатка", "UF_NUMBER": "26_777"}) == "26_777"


def test_production_years_filter(monkeypatch, bitrix):
    monkeypatch.setattr(settings, "bitrix_production_years", "26, 27")

    assert hooks.extract_production_number({"TITLE": "26_001 А"}) == "26_001"
    assert hooks.extract_production_number({"TITLE": "25_001 А"}) is None


# --- разбор значений ---

def test_first_filled_takes_first_non_empty_and_unwraps_lists():
    d = {"A": "", "B": [], "C": ["2026-08-15T10:00:00+03:00", "x"], "D": "последнее"}

    assert hooks._first_filled(d, "A, B, C, D") == "2026-08-15T10:00:00+03:00"
    assert hooks._first_filled(d, "A,B") is None
    assert hooks._first_filled({"A": False}, "A") is None
    assert hooks._first_filled(d, "") is None


@pytest.mark.parametrize("raw,expected", [
    ("2026-08-15T03:00:00+03:00", datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)),
    ("15.08.2026", datetime(2026, 8, 15, tzinfo=timezone.utc)),
    ("15.08.2026 10:30:00", datetime(2026, 8, 15, 10, 30, tzinfo=timezone.utc)),
    ("мусор", None),
    ("", None),
    (None, None),
    (12345, None),
])
def test_parse_bitrix_datetime(raw, expected):
    parsed = hooks._parse_bitrix_datetime(raw)
    if expected is None:
        assert parsed is None
    else:
        assert parsed == expected


def test_extract_matches_nested_form_keys_by_suffix():
    form = {"data[FIELDS][ID]": "42", "auth[application_token]": "tok"}

    assert hooks._extract(form, "[id]") == "42"
    assert hooks._extract(form, "[application_token]") == "tok"
    assert hooks._extract(form, "[missing]") is None


def test_verify_token(monkeypatch):
    monkeypatch.setattr(settings, "bitrix_incoming_webhook_tokens", " first , second ")

    assert hooks.verify_token({"auth[application_token]": "second"}) is True
    assert hooks.verify_token({"auth[application_token]": "wrong"}) is False
    assert hooks.verify_token({}) is False
    monkeypatch.setattr(settings, "bitrix_incoming_webhook_tokens", "")
    assert hooks.verify_token({"auth[application_token]": ""}) is False
    assert hooks.verify_token({"auth[application_token]": "anything"}) is False


# --- создание проекта ---

async def test_new_deal_creates_project(db_session, bitrix):
    project, created = await hooks.upsert_project_from_deal(db_session, deal(
        UF_PLAN="2026-09-01T00:00:00+03:00", UF_OLD_FACT="15.09.2026",
    ))

    assert created is True
    assert project.name == "26_138 ЖК Сосны, ул. Ленина 5"
    assert project.production_number == "26_138"
    assert project.bitrix_deal_id == "501"
    assert project_code_service.decrypt_project_code(project.unique_code) == "26_138"
    # год в названии папки на NAS лишний — она и так лежит в годовой папке
    assert project.folder_name == "138 ЖК Сосны, ул. Ленина 5"
    assert project.shipment_planned_at == datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc)
    assert project.shipment_actual_at == datetime(2026, 9, 15, tzinfo=timezone.utc)
    assert bitrix.created_folders == [project.id]
    assert bitrix.synced_folders == []


async def test_deal_without_number_is_skipped(db_session, bitrix):
    project, created = await hooks.upsert_project_from_deal(db_session, deal(title="Без номера"))

    assert (project, created) == (None, False)
    assert bitrix.created_folders == []


async def test_missing_encryption_key_creates_nothing(db_session, monkeypatch, bitrix):
    monkeypatch.setattr(settings, "bitrix_project_code_key", "")

    project, created = await hooks.upsert_project_from_deal(db_session, deal())

    assert (project, created) == (None, False)
    assert await _projects(db_session, "26_138") == []


# --- повторные события ---

async def test_same_deal_twice_is_idempotent(db_session, bitrix):
    first, _ = await hooks.upsert_project_from_deal(db_session, deal())

    second, created = await hooks.upsert_project_from_deal(db_session, deal())

    assert created is False
    assert second.id == first.id
    assert len(await _projects(db_session, "26_138")) == 1
    assert bitrix.created_folders == [first.id]  # папку заводили один раз
    assert bitrix.synced_folders == []           # название не менялось — переименовывать нечего


async def test_renamed_deal_updates_name_and_schedules_folder_sync(db_session, bitrix):
    first, _ = await hooks.upsert_project_from_deal(db_session, deal())

    renamed, created = await hooks.upsert_project_from_deal(db_session, deal(title="26_138 ЖК Берёзы"))

    assert created is False and renamed.id == first.id
    assert renamed.name == "26_138 ЖК Берёзы"
    assert bitrix.synced_folders == [first.id]


async def test_two_deals_with_same_number_become_two_projects(db_session, bitrix):
    a, _ = await hooks.upsert_project_from_deal(db_session, deal("501"))
    b, created = await hooks.upsert_project_from_deal(db_session, deal("502"))

    assert created is True and a.id != b.id
    assert {p.bitrix_deal_id for p in await _projects(db_session, "26_138")} == {"501", "502"}


async def test_third_deal_with_same_number_still_works(db_session, bitrix):
    """Дубль номера — не редкость (опечатка в CRM). Третья сделка с тем же
    номером не должна валить обработку, а пятое событие по первой — тем более."""
    await hooks.upsert_project_from_deal(db_session, deal("501"))
    await hooks.upsert_project_from_deal(db_session, deal("502"))

    third, created = await hooks.upsert_project_from_deal(db_session, deal("503"))
    again, created_again = await hooks.upsert_project_from_deal(db_session, deal("501"))

    assert created is True and third.bitrix_deal_id == "503"
    assert created_again is False and again.bitrix_deal_id == "501"
    assert len(await _projects(db_session, "26_138")) == 3


async def test_project_without_deal_id_adopts_the_first_matching_deal(db_session, make_project, bitrix):
    old = await make_project(name="Старый проект", production_number="26_138", unique_code="old-code")

    project, created = await hooks.upsert_project_from_deal(db_session, deal("501"))

    assert created is False
    assert project.id == old.id
    assert project.bitrix_deal_id == "501"
    assert project.name == "26_138 ЖК Сосны, ул. Ленина 5"


async def test_deleted_project_is_resurrected_by_its_deal(db_session, bitrix):
    project, _ = await hooks.upsert_project_from_deal(db_session, deal())
    project.deleted_at = datetime.now(timezone.utc)
    await db_session.flush()
    bitrix.synced_folders.clear()

    resurrected, created = await hooks.upsert_project_from_deal(db_session, deal())

    assert created is False and resurrected.id == project.id
    assert resurrected.deleted_at is None
    assert bitrix.synced_folders == [project.id]


async def test_deal_does_not_touch_warranty(db_session, bitrix):
    project, _ = await hooks.upsert_project_from_deal(db_session, deal())
    project.warranty_starts_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    project.warranty_ends_at = datetime(2028, 1, 1, tzinfo=timezone.utc)
    await db_session.flush()

    await hooks.upsert_project_from_deal(db_session, deal(UF_PLAN="2026-12-01T00:00:00+03:00"))

    assert project.warranty_starts_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert project.warranty_ends_at == datetime(2028, 1, 1, tzinfo=timezone.utc)


# --- компания ---

async def test_company_name_is_taken_from_bitrix(db_session, bitrix):
    bitrix.companies["77"] = "ООО Ромашка"

    project, _ = await hooks.upsert_project_from_deal(db_session, deal(COMPANY_ID="77"))

    assert project.bitrix_company_id == "77"
    assert project.company_name == "ООО Ромашка"


async def test_failed_company_lookup_keeps_known_name(db_session, bitrix):
    bitrix.companies["77"] = "ООО Ромашка"
    project, _ = await hooks.upsert_project_from_deal(db_session, deal(COMPANY_ID="77"))
    bitrix.companies.clear()  # Bitrix не ответил

    await hooks.upsert_project_from_deal(db_session, deal(COMPANY_ID="77"))

    assert project.company_name == "ООО Ромашка"


@pytest.mark.parametrize("company_id", ["0", "", None])
async def test_deal_without_company_clears_company(db_session, bitrix, company_id):
    bitrix.companies["77"] = "ООО Ромашка"
    project, _ = await hooks.upsert_project_from_deal(db_session, deal(COMPANY_ID="77"))

    await hooks.upsert_project_from_deal(db_session, deal(COMPANY_ID=company_id))

    assert project.bitrix_company_id is None and project.company_name is None


# --- контактные лица ---

async def test_contacts_are_replaced_by_the_deal_set(db_session, bitrix):
    bitrix.contacts["1"] = {"bitrix_contact_id": "1", "full_name": "Кузнецов А.", "phones": ["+375291112233"]}
    bitrix.contacts["2"] = {"bitrix_contact_id": "2", "full_name": "Петров П.", "emails": ["p@example.by"]}
    bitrix.deal_contacts["501"] = ["1", "2"]
    project, _ = await hooks.upsert_project_from_deal(db_session, deal("501"))

    repo = ProjectContactRepository(db_session)
    assert [c.full_name for c in await repo.list_for_project(project.id)] == ["Кузнецов А.", "Петров П."]

    bitrix.deal_contacts["501"] = ["2"]  # первого из сделки убрали
    await hooks.upsert_project_from_deal(db_session, deal("501"))

    assert [c.full_name for c in await repo.list_for_project(project.id)] == ["Петров П."]


async def test_unreadable_contact_is_skipped(db_session, bitrix):
    bitrix.contacts["2"] = {"bitrix_contact_id": "2", "full_name": "Петров П."}
    bitrix.deal_contacts["501"] = ["1", "2"]  # контакт 1 не прочитался

    project, _ = await hooks.upsert_project_from_deal(db_session, deal("501"))

    contacts = await ProjectContactRepository(db_session).list_for_project(project.id)
    assert [c.full_name for c in contacts] == ["Петров П."]


async def test_bitrix_failure_keeps_existing_contacts(db_session, bitrix):
    bitrix.contacts["1"] = {"bitrix_contact_id": "1", "full_name": "Кузнецов А."}
    bitrix.deal_contacts["501"] = ["1"]
    project, _ = await hooks.upsert_project_from_deal(db_session, deal("501"))
    bitrix.deal_contacts["501"] = None  # список контактов сделки не получили

    await hooks.upsert_project_from_deal(db_session, deal("501"))

    contacts = await ProjectContactRepository(db_session).list_for_project(project.id)
    assert [c.full_name for c in contacts] == ["Кузнецов А."]


# --- вебхук целиком ---

async def test_webhook_loads_the_deal_and_creates_project(db_session, monkeypatch, bitrix):
    monkeypatch.setattr(hooks, "AsyncSessionLocal", lambda: _SessionContext(db_session))

    async def get_deal(deal_id):
        return deal(deal_id, "26_555 Новый объект")

    monkeypatch.setattr(bitrix_service, "get_deal", get_deal)

    await hooks.handle_deal_event({"data[FIELDS][ID]": "900"})

    [project] = await _projects(db_session, "26_555")
    assert project.bitrix_deal_id == "900"


@pytest.mark.parametrize("form", [{}, {"data[FIELDS][OTHER]": "1"}])
async def test_webhook_without_deal_id_does_nothing(db_session, monkeypatch, bitrix, form):
    called = []

    async def get_deal(deal_id):
        called.append(deal_id)

    monkeypatch.setattr(bitrix_service, "get_deal", get_deal)

    await hooks.handle_deal_event(form)

    assert called == []


async def test_webhook_when_deal_cannot_be_loaded_creates_nothing(db_session, monkeypatch, bitrix):
    monkeypatch.setattr(hooks, "AsyncSessionLocal", lambda: _SessionContext(db_session))

    async def get_deal(deal_id):
        return None

    monkeypatch.setattr(bitrix_service, "get_deal", get_deal)

    await hooks.handle_deal_event({"data[FIELDS][ID]": "900"})

    assert bitrix.created_folders == []
