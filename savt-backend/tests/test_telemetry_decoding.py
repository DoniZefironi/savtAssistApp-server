"""Расшифровка регистров телеметрии ШУ — побитово, с картой названий
(стандартная карта + переопределения конкретного ШУ поверх). Пока в основном
чистая логика (_register_bits/_named_bit_transitions/_decode_registers),
кроме сборки самой карты (_build_name_map) — она нужна реальная БД, раз
проверяет приоритет override над стандартной картой.
"""
from app.repositories.telemetry import CabinetRegisterOverrideRepository, RegisterDefinitionRepository
from app.services.telemetry_service import (
    _build_name_map,
    _decode_registers,
    _named_bit_transitions,
    _register_bits,
)


# --- _register_bits ---

def test_register_bits_zero_is_all_zero():
    assert _register_bits(0) == [0] * 16


def test_register_bits_single_bit_set():
    bits = _register_bits(1)
    assert bits[0] == 1
    assert sum(bits) == 1


def test_register_bits_multiple_bits():
    # 0b1010 = 10 -> биты 1 и 3 взведены
    bits = _register_bits(0b1010)
    assert bits[1] == 1 and bits[3] == 1
    assert sum(bits) == 2


def test_register_bits_masks_signed_negative_one_to_all_ones():
    # контроллер может прислать регистр как знаковое число (-32768..32767),
    # не беззнаковое — см. комментарий в коде про & 0xFFFF
    assert _register_bits(-1) == [1] * 16


def test_register_bits_masks_signed_min_to_bit_15_only():
    bits = _register_bits(-32768)  # 0x8000 как знаковое
    assert bits == [0] * 15 + [1]


# --- _named_bit_transitions ---

def test_no_transitions_when_value_unchanged():
    name_map = {(501, 0): "Авария насоса"}
    assert _named_bit_transitions(5, 5, 501, name_map) == []


def test_transition_on_unnamed_bit_is_excluded():
    assert _named_bit_transitions(0, 1, 501, {}) == []  # бит 0 изменился, но не назван


def test_transition_on_named_bit_is_included():
    name_map = {(501, 0): "Авария насоса"}
    result = _named_bit_transitions(0, 1, 501, name_map)
    assert result == [(0, "Авария насоса", 0, 1)]


def test_transition_includes_both_directions():
    name_map = {(501, 0): "Авария насоса"}
    assert _named_bit_transitions(1, 0, 501, name_map) == [(0, "Авария насоса", 1, 0)]


def test_only_named_bits_among_several_changed():
    name_map = {(501, 2): "Авария клапана"}
    # биты 0 и 2 меняются, только бит 2 назван
    result = _named_bit_transitions(0b000, 0b101, 501, name_map)
    assert result == [(2, "Авария клапана", 0, 1)]


# --- _decode_registers ---

def test_decode_default_shows_only_named_and_active_bits():
    name_map = {(501, 0): "Авария насоса", (501, 1): "Авария клапана"}
    # бит 0: назван и взведён -> показать; бит 1: назван, но НЕ взведён -> скрыть
    registers = _decode_registers({"501": 0b01}, name_map, include_unnamed=False)
    assert [(r.address, r.bit, r.name, r.value) for r in registers] == [(501, 0, "Авария насоса", 1)]


def test_decode_default_hides_unnamed_bits_even_if_active():
    registers = _decode_registers({"501": 0b1}, {}, include_unnamed=False)
    assert registers == []


def test_decode_include_unnamed_shows_all_16_bits():
    registers = _decode_registers({"501": 0}, {}, include_unnamed=True)
    assert len(registers) == 16
    assert all(r.name is None and r.value == 0 for r in registers)


def test_decode_converts_string_address_key_to_int():
    # ключи JSONB всегда строки — адрес должен вернуться числом
    registers = _decode_registers({"501": 0}, {}, include_unnamed=True)
    assert all(isinstance(r.address, int) for r in registers)
    assert registers[0].address == 501


# --- _build_name_map: override важнее стандартной карты, не течёт между ШУ ---

async def test_name_map_uses_standard_definition_when_no_override(db_session, make_cabinet):
    cabinet = await make_cabinet()
    db_session.add_all([_definition(501, 0, "Авария насоса")])
    await db_session.flush()

    name_map = await _build_name_map(
        RegisterDefinitionRepository(db_session), CabinetRegisterOverrideRepository(db_session), cabinet.id,
    )
    assert name_map[(501, 0)] == "Авария насоса"


async def test_name_map_override_wins_over_standard_definition(db_session, make_cabinet):
    cabinet = await make_cabinet()
    db_session.add_all([_definition(501, 0, "Авария насоса")])
    await db_session.flush()
    db_session.add(_override(cabinet.id, 501, 0, "Своё название для этого ШУ"))
    await db_session.flush()

    name_map = await _build_name_map(
        RegisterDefinitionRepository(db_session), CabinetRegisterOverrideRepository(db_session), cabinet.id,
    )
    assert name_map[(501, 0)] == "Своё название для этого ШУ"


async def test_name_map_override_can_add_bit_not_in_standard_map(db_session, make_cabinet):
    cabinet = await make_cabinet()
    db_session.add(_override(cabinet.id, 501, 5, "Добавка только для этого ШУ"))
    await db_session.flush()

    name_map = await _build_name_map(
        RegisterDefinitionRepository(db_session), CabinetRegisterOverrideRepository(db_session), cabinet.id,
    )
    assert name_map[(501, 5)] == "Добавка только для этого ШУ"


async def test_name_map_override_does_not_leak_to_other_cabinet(db_session, make_cabinet):
    cabinet_a = await make_cabinet()
    cabinet_b = await make_cabinet()
    db_session.add(_override(cabinet_a.id, 501, 0, "Только для A"))
    await db_session.flush()

    name_map_b = await _build_name_map(
        RegisterDefinitionRepository(db_session), CabinetRegisterOverrideRepository(db_session), cabinet_b.id,
    )
    assert (501, 0) not in name_map_b


def _definition(address, bit, name):
    from app.models.register_definition import RegisterDefinition
    return RegisterDefinition(address=address, bit=bit, name=name)


def _override(cabinet_id, address, bit, name):
    from app.models.cabinet_register_override import CabinetRegisterOverride
    return CabinetRegisterOverride(cabinet_id=cabinet_id, address=address, bit=bit, name=name)
