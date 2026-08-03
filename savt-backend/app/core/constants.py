from enum import StrEnum


class RoleName(StrEnum):
    USER = "user"
    OPERATOR = "operator"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"
    BOT = "bot"


# Служебные учётки, от лица которых пишут бот и интеграция с Bitrix. В списках
# пользователей админки не показываются: это не сотрудники, управлять ими нельзя.
#
# Роль тут не помогает: у бота она "bot" и скрывается сама, а вот пользователь
# Bitrix заведён с ролью "operator" намеренно — иначе ChatService не пустил бы
# его в чаты заявок, куда он пересылает комментарии из задач. Из-за этого он и
# показывался в списке операторов под именем "Ася".
BOT_USER_LOGIN = "__ася__"
BITRIX_USER_LOGIN = "__bitrix__"
SYSTEM_USER_LOGINS = (BOT_USER_LOGIN, BITRIX_USER_LOGIN)