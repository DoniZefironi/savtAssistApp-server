"""Права каждого маршрута приложения, вычисленные из самого кода: какая роль
минимально допущена. Нужен тесту матрицы прав (test_route_permissions.py) и
скрипту, который обновляет снимок tests/route_permissions.txt:

    python -m tests.route_permissions > tests/route_permissions.txt
"""
import inspect

from fastapi.routing import APIRoute

ROLES = ["user", "operator", "admin", "superadmin"]

# уровни: public — вход не нужен; user_or_guest — подойдёт и гостевой токен;
# authenticated — любой вошедший пользователь; дальше минимальная роль
PUBLIC = "public"
USER_OR_GUEST = "user_or_guest"
AUTHENTICATED = "authenticated"


def _walk(dependant):
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


def classify(route: APIRoute) -> str:
    allowed: set[str] | None = None
    has_user = False
    has_guest = False
    for dep in _walk(route.dependant):
        call = dep.call
        name = getattr(call, "__name__", "")
        module = getattr(call, "__module__", "")
        if module != "app.core.dependencies":
            continue
        if name == "checker":
            roles = set(inspect.getclosurevars(call).nonlocals["expanded"])
            allowed = roles if allowed is None else allowed & roles
        elif name == "get_current_user":
            has_user = True
        elif name == "get_current_user_or_guest":
            has_guest = True

    if allowed is not None:
        return next(role for role in ROLES if role in allowed) if allowed else "nobody"
    if has_user:
        return AUTHENTICATED
    if has_guest:
        return USER_OR_GUEST
    return PUBLIC


def compute(app) -> dict[tuple[str, str], str]:
    result = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            result[(method, route.path)] = classify(route)
    return result


def render(permissions: dict[tuple[str, str], str]) -> str:
    lines = [f"{method:6} {path}  {level}" for (method, path), level in sorted(permissions.items(), key=lambda x: (x[0][1], x[0][0]))]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    from app.main import app

    print(render(compute(app)), end="")
