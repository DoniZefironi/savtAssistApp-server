"""Поля входных данных без ограничения размера, найденные по коду всех маршрутов. Нужен тесту
test_input_field_limits.py; руками: `python -m tests.input_limits`.

Входные данные — тела запросов (pydantic-модели, в том числе вложенные) и поля форм. Параметры
пути и запроса сюда не входят: их длину и так режет nginx (заголовок запроса до 8 КБ).

Поле считается ограниченным, если это:
  - строка с max_length или с шаблоном вида ^(a|b|c)$ (закрытый перечень значений);
  - список с max_length;
  - словарь с json_size_limit / dict_items_limit (app/schemas/limits.py).
Всё остальное (строка, список, словарь, Any без предела) — находка."""
import re
import typing
from collections import defaultdict

import annotated_types
from pydantic import BaseModel

from tests.route_permissions import _api_routes

_NONE = type(None)
_CLOSED_PATTERN = re.compile(r"^\^\(?[\w\-|]+\)?\$$")


def _strip(annotation):
    """Optional[X] и Annotated[X, ...] -> X."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _strip(typing.get_args(annotation)[0])
    if origin is typing.Union or (origin is not None and origin.__name__ == "UnionType"):
        args = [a for a in typing.get_args(annotation) if a is not _NONE]
        return _strip(args[0]) if len(args) == 1 else tuple(_strip(a) for a in args)
    return annotation


def _has_max_length(metadata) -> bool:
    return any(isinstance(m, annotated_types.MaxLen) for m in metadata)


def _has_closed_pattern(metadata) -> bool:
    return any(_CLOSED_PATTERN.match(getattr(m, "pattern", None) or "") for m in metadata)


def _is_size_limited(metadata) -> bool:
    return any(getattr(getattr(m, "func", None), "size_limited", False) for m in metadata)


def _problem(info) -> str | None:
    annotation = _strip(info.annotation)
    metadata = info.metadata
    if annotation is str:
        return None if _has_max_length(metadata) or _has_closed_pattern(metadata) else "строка без max_length"
    if annotation is dict or typing.get_origin(annotation) is dict or annotation is typing.Any:
        return None if _is_size_limited(metadata) else "словарь без json_size_limit"
    if annotation is list or typing.get_origin(annotation) is list:
        return None if _has_max_length(metadata) else "список без max_length"
    return None


def _walk(model, prefix, seen, found):
    if model in seen:
        return
    for name, info in model.model_fields.items():
        problem = _problem(info)
        if problem:
            found.append((f"{model.__name__}.{prefix}{name}", problem))
        annotation = _strip(info.annotation)
        candidates = list(typing.get_args(annotation)) if typing.get_origin(annotation) is list else [annotation]
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                _walk(candidate, f"{prefix}{name}.", seen | {model}, found)


def unbounded_input_fields(app) -> dict[str, list[str]]:
    """{'Модель.поле (проблема)': [маршруты, где оно принимается]}."""
    found_at = defaultdict(set)
    for route in _api_routes(app):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            where = f"{method} {route.path}"
            for param in route.dependant.body_params:
                info = param.field_info
                annotation = _strip(info.annotation)
                if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    found = []
                    _walk(annotation, "", frozenset(), found)
                    for field, problem in found:
                        found_at[f"{field} ({problem})"].add(where)
                elif type(info).__name__ == "Form" and annotation is str and not _has_max_length(info.metadata):
                    found_at[f"форма {param.name} (строка без max_length)"].add(where)
    return {field: sorted(routes) for field, routes in sorted(found_at.items())}


if __name__ == "__main__":
    from app.main import app

    result = unbounded_input_fields(app)
    for field, routes in result.items():
        print(f"{field}\n    {', '.join(routes[:3])}")
    print(f"\nвсего: {len(result)}")
