"""Какие отделы Bitrix дают какую роль в нашей системе (ID отдела -> роль).
Отдел, которого здесь нет (например, "Отпуск по уходу за ребёнком"), роли не
даёт. Если сотрудник состоит в нескольких отделах — берётся высшая роль."""
from app.core.constants import RoleName

DEPARTMENT_ROLES: dict[int, RoleName] = {
    1: RoleName.SUPERADMIN,    # Директор
    69: RoleName.OPERATOR,     # Бухгалтерия
    73: RoleName.SUPERADMIN,   # Главный инженер
    27: RoleName.OPERATOR,     # Сектор наладки
    23: RoleName.OPERATOR,     # Сектор проектирования
    25: RoleName.ADMIN,        # Сектор разработки программного обеспечения
    95: RoleName.SUPERADMIN,   # Заместитель главного инженера по производству
    45: RoleName.OPERATOR,     # Производственный участок
    57: RoleName.OPERATOR,     # Сектор конструкторско-технологических разработок
    63: RoleName.OPERATOR,     # Участок механического производства
    3: RoleName.ADMIN,         # Заместитель директора по коммерческим вопросам
    91: RoleName.OPERATOR,     # Коммерческий сектор
    101: RoleName.OPERATOR,    # Сектор снабжения
    97: RoleName.OPERATOR,     # Склад
    71: RoleName.OPERATOR,     # Менеджер по персоналу
    65: RoleName.ADMIN,        # Помощник директора
}

# Сотрудники Bitrix, которым синхронизация роль не даёт, даже если отдел её
# даёт (ID пользователя в Bitrix)
EXCLUDED_BITRIX_USER_IDS: set[int] = set()

_RANK = {RoleName.OPERATOR: 1, RoleName.ADMIN: 2, RoleName.SUPERADMIN: 3}


def role_for_departments(department_ids: list[int]) -> RoleName | None:
    roles = [DEPARTMENT_ROLES[d] for d in department_ids if d in DEPARTMENT_ROLES]
    return max(roles, key=_RANK.__getitem__) if roles else None


def role_rank(role: str) -> int:
    return _RANK.get(RoleName(role), 0) if role in {r.value for r in RoleName} else 0
