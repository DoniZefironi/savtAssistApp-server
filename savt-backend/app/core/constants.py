from enum import StrEnum


class RoleName(StrEnum):
    USER = "user"
    OPERATOR = "operator"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"
    BOT = "bot"


# Сотрудники: оператор, администратор, суперадмин (бот и обычные пользователи не входят)
STAFF_ROLE_NAMES = (RoleName.OPERATOR.value, RoleName.ADMIN.value, RoleName.SUPERADMIN.value)

BOT_USER_LOGIN = "__ася__"
BITRIX_USER_LOGIN = "__bitrix__"
SYSTEM_USER_LOGINS = (BOT_USER_LOGIN, BITRIX_USER_LOGIN)