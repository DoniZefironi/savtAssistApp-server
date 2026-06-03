from enum import StrEnum


class RoleName(StrEnum):
    USER = "user"
    OPERATOR = "operator"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"
    BOT = "bot"