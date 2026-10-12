"""Проверка настроек безопасности: что остаётся слабым или выключенным. Проблемы
пишутся в лог при старте и выводятся командой `python -m app.cli check-config`
(её удобно запускать перед выкаткой на сервер, не поднимая приложение)."""
from app.config import Settings

_PLACEHOLDER_PREFIX = "change_me"
_MIN_SECRET_LENGTH = 32
_PRODUCTION_ENVS = ("prod", "production")


def is_production(app_env: str) -> bool:
    return app_env.strip().lower() in _PRODUCTION_ENVS


def docs_enabled(app_env: str) -> bool:
    """Swagger и схема API (/docs, /redoc, /openapi.json) открывают полный перечень
    ручек любому, кто знает адрес, поэтому в проде они выключены."""
    return not is_production(app_env)


def _is_placeholder(value: str) -> bool:
    return not value or value.startswith(_PLACEHOLDER_PREFIX)


def config_problems(settings: Settings) -> list[str]:
    """Список слабых мест настроек, по одному сообщению на каждое."""
    problems: list[str] = []

    if _is_placeholder(settings.jwt_secret_key) or len(settings.jwt_secret_key) < _MIN_SECRET_LENGTH:
        problems.append(
            "JWT_SECRET_KEY короче 32 символов или остался значением по умолчанию: "
            "по слабому ключу подделываются токены доступа"
        )
    if _is_placeholder(settings.static_link_secret):
        problems.append(
            "STATIC_LINK_SECRET не задан или остался значением по умолчанию: ссылки на файлы "
            "подписываются предсказуемо либо не подписываются вовсе, скачивание фактически открыто"
        )
    elif len(settings.static_link_secret) < _MIN_SECRET_LENGTH:
        problems.append("STATIC_LINK_SECRET короче 32 символов: подпись ссылок на файлы можно подобрать")

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if "*" in origins:
        problems.append(
            "CORS_ORIGINS=*: любой сайт может обращаться к API из браузера пользователя; "
            "перечислите адреса панели и веб-версии приложения"
        )

    if not is_production(settings.app_env):
        problems.append(
            f"APP_ENV={settings.app_env!r}: в боевом окружении должно быть prod "
            "(иначе /docs открыт и SQL-запросы пишутся в лог)"
        )

    return problems
