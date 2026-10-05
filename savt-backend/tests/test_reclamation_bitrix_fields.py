"""Сборка нативных полей Bitrix из object_type/object_details (_build_bitrix_native_fields).

Это та самая логика, из-за пустоты которой карточка реально уезжала в Bitrix
с незаполненным "Заводской номер ШУ или линии"/"Данные ПКИ" (обнаружено
тестовой рекламацией №44, см. README) — ровно то место, где стоит закрепить
поведение тестом, а не полагаться на то, что кто-то снова заметит пустое поле
в карточке руками.
"""
from app.services.reclamation_service import _build_bitrix_native_fields


def test_cabinet_takes_serial_number_from_object_details():
    serial, _, component = _build_bitrix_native_fields(
        "cabinet", {"serial_number": "29_099"}, None, None, None,
    )
    assert serial == "29_099"
    assert component is None


def test_cabinet_without_object_details_gives_empty_serial():
    # это ровно тот случай, что валидация на создании рекламации теперь блокирует
    # заранее (см. ReclamationService.create) — здесь фиксируем, что сама сборка
    # полей на пустом object_details не падает, а просто ничего не находит
    serial, _, _ = _build_bitrix_native_fields("cabinet", None, None, None, None)
    assert serial is None


def test_line_takes_serial_number_from_object_details():
    serial, _, component = _build_bitrix_native_fields(
        "line", {"serial_number": "AL-2026-014"}, None, None, None,
    )
    assert serial == "AL-2026-014"
    assert component is None


def test_line_without_object_details_gives_empty_serial():
    serial, _, _ = _build_bitrix_native_fields("line", None, None, None, None)
    assert serial is None


def test_component_joins_only_present_fields():
    _, _, component = _build_bitrix_native_fields(
        "component",
        {"name": "Контактор", "model": "LC1D18", "article": None, "serial_number": "SN-1"},
        None, None, None,
    )
    assert component == "наименование: Контактор, модель: LC1D18, серийный номер: SN-1"


def test_component_all_fields_empty_gives_none_not_empty_string():
    _, _, component = _build_bitrix_native_fields(
        "component", {"name": None, "model": None, "article": None, "serial_number": None},
        None, None, None,
    )
    assert component is None


def test_software_and_documentation_produce_no_serial_or_component():
    # для этих типов object_details не отображается ни в одно из трёх новых полей
    for object_type in ("software", "documentation"):
        serial, _, component = _build_bitrix_native_fields(
            object_type, {"info": "что угодно"}, None, None, None,
        )
        assert serial is None
        assert component is None


def test_contract_info_joins_only_present_parts():
    _, contract, _ = _build_bitrix_native_fields(
        "line", {"serial_number": "x"}, "Д-100", None, "ТТН-300",
    )
    assert contract == "Договор: Д-100, ТТН/CMR: ТТН-300"


def test_contract_info_none_when_nothing_provided():
    _, contract, _ = _build_bitrix_native_fields("line", {"serial_number": "x"}, None, None, None)
    assert contract is None
