"""Ограничение размера свободных словарей во входных схемах. Строкам и спискам хватает
max_length в Field, а словарь (JSON) без предела сохранялся бы в БД любого размера.

Использование: `details: Annotated[dict[str, Any] | None, json_size_limit(2048)] = None`.
Тест tests/test_input_field_limits.py считает такое поле ограниченным."""
import json

from pydantic import AfterValidator


def json_size_limit(max_bytes: int) -> AfterValidator:
    """Размер словаря в JSON (UTF-8) не больше max_bytes байт."""
    def check(value):
        if value is not None and len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > max_bytes:
            raise ValueError(f"Слишком большой объект: не больше {max_bytes} байт в JSON")
        return value

    check.size_limited = True
    return AfterValidator(check)


def dict_items_limit(max_items: int) -> AfterValidator:
    """Число ключей словаря не больше max_items."""
    def check(value):
        if value is not None and len(value) > max_items:
            raise ValueError(f"Слишком много элементов: не больше {max_items}")
        return value

    check.size_limited = True
    return AfterValidator(check)
