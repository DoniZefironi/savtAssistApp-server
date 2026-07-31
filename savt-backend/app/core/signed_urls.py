"""Подпись ссылок на файлы в /static/ (nginx secure_link).

Раньше /static/ раздавался nginx-ом напрямую и без авторизации: ссылка на файл
работала вечно, у кого угодно, и по сути была бессрочным bearer-токеном на этот
файл. При этом ссылки утекают наружу — например, sync_message_to_bitrix кладёт
их открытым текстом в комментарии Bitrix-задач.

Теперь URL действителен ограниченное время и только с подписью. Проверяет её
nginx (см. блоки location /static/ в nginx*.conf), приложение в раздаче файла
не участвует — поэтому картинки по-прежнему грузятся тегом <img> без заголовка
Authorization, а ссылки в Bitrix открываются людьми без сессии в нашем API.

Важно: в БД URL хранится ГОЛЫМ, без подписи. Подпись проставляется в момент
сериализации ответа (см. SignedUrl/SignedUrlOpt), а на входе снимается
(см. strip_signature) — иначе клиент, вернувший нам полученный от нас URL,
записал бы подпись в базу, и она протухла бы вместе с записью.
"""
import base64
import hashlib
import logging
import time
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import PlainSerializer

from app.config import settings

_log = logging.getLogger(__name__)

STATIC_PREFIX = "/static/"


def strip_signature(url: str | None) -> str | None:
    """Возвращает голый путь без query-параметров подписи.

    Применяется ко всему, что приходит от клиента и попадает в БД: клиент
    оперирует теми URL, которые получил от нас, то есть уже подписанными."""
    if not url:
        return url
    return urlsplit(url).path or url


def sign_url(url: str | None, ttl_seconds: int | None = None) -> str | None:
    """Добавляет к /static/-ссылке подпись и срок годности.

    Всё, что не начинается с /static/ (внешние ссылки, None), возвращается
    как есть — так что функцию безопасно вешать на поле, куда может прийти
    и не наш URL."""
    if not url or not url.startswith(STATIC_PREFIX):
        return url

    secret = settings.static_link_secret
    if not secret:
        # Локальный запуск без nginx: подписывать нечем и незачем. На стенде,
        # где nginx подпись проверяет, пустой секрет означал бы 403 на все файлы.
        _log.warning("STATIC_LINK_SECRET не задан — ссылка %s отдана без подписи", url)
        return url

    path = strip_signature(url)
    expires = int(time.time()) + (ttl_seconds or settings.static_link_ttl_seconds)
    # Строка обязана в точности совпадать с secure_link_md5 в nginx*.conf:
    #   secure_link_md5 "$secure_link_expires$uri ${STATIC_LINK_SECRET}";
    # Пробел перед секретом обязателен, и не только для читаемости: секрет
    # подставляется в конфиг через envsubst, и без разделителя nginx прочитает
    # "$uri" + первые буквы секрета как одно имя переменной ($uritest...) и
    # не запустится вовсе. Секрет — в конце строки, иначе конструкция была бы
    # уязвима к length-extension.
    digest = hashlib.md5(f"{expires}{path} {secret}".encode("utf-8")).digest()
    # nginx secure_link ждёт base64url без "=" (см. secure_link_md5)
    signature = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{path}?md5={signature}&expires={expires}"


def sign_url_long(url: str | None) -> str | None:
    """Подпись с увеличенным сроком — для ссылок, уходящих во внешние системы
    (комментарии Bitrix-задач), где их открывают спустя дни после отправки."""
    return sign_url(url, ttl_seconds=settings.static_link_external_ttl_seconds)


def _sign_required(value: str) -> str:
    return sign_url(value) or value


def _sign_optional(value: str | None) -> str | None:
    return sign_url(value)


# Типы для полей схем, отдающих ссылку на файл наружу. Подпись проставляется
# при сериализации ответа — в том числе при model_dump(mode="json") для SSE,
# так что live-события несут такие же рабочие ссылки, как и REST-ответы.
SignedUrl = Annotated[str, PlainSerializer(_sign_required, return_type=str)]
SignedUrlOpt = Annotated[str | None, PlainSerializer(_sign_optional, return_type=str | None)]
