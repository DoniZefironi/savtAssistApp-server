import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import BITRIX_USER_LOGIN as _INCOMING_USER_LOGIN

_log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

# Отображается в чате как имя отправителя пересланных из Bitrix сообщений —
# "Ася", чтобы визуально выглядело как ответ бота (сам текст сообщения при
# этом явно поясняет, что ответ на самом деле от оператора, см. handle_task_comment_webhook)
_INCOMING_USER_NAME = "Ася"

# Статусы задач Bitrix24 (поле STATUS в tasks.task.*): 1 Новая, 2 Ждёт выполнения,
# 3 Выполняется, 4 Ждёт контроля, 5 Завершена, 6 Отложена, 7 Отклонена
_STATUS_TO_BITRIX = {
    "open": "2",
    "in_progress": "3",
    "postponed": "6",
    "closed": "5",
}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15)
    return _client


async def create_task(title: str, description: str) -> str | None:
    """Создаёт задачу в Bitrix24 через входящий вебхук (tasks.task.add).
    Возвращает ID созданной задачи, либо None если Bitrix не настроен
    (тогда заявка в нашей БД создаётся как обычно, просто без синхронизации)."""
    if not settings.bitrix_webhook_url or not settings.bitrix_default_responsible_id:
        return None

    fields = {
        "TITLE": title,
        "DESCRIPTION": description,
        "RESPONSIBLE_ID": settings.bitrix_default_responsible_id,
    }
    if settings.bitrix_default_group_id:
        fields["GROUP_ID"] = settings.bitrix_default_group_id
    if settings.bitrix_default_creator_id:
        fields["CREATED_BY"] = settings.bitrix_default_creator_id

    url = f"{settings.bitrix_webhook_url.rstrip('/')}/tasks.task.add.json"
    resp = await _get_client().post(url, json={"fields": fields})
    if not resp.is_success:
        raise RuntimeError(f"Bitrix tasks.task.add {resp.status_code}: {resp.text}")
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Bitrix tasks.task.add error: {data}")
    return str(data["result"]["task"]["id"])


async def add_comment(task_id: str, text: str) -> None:
    """Отправляет сообщение в чат задачи (tasks.task.chat.message.send) —
    используется для синхронизации сообщений заявителя из чата заявки в задачу.
    Старый task.commentitem.add не работает с новой карточкой задачи, где
    комментарии физически хранятся в чате (IM), см. get_task_chat_id/get_dialog_message."""
    if not settings.bitrix_webhook_url:
        return

    url = f"{settings.bitrix_webhook_url.rstrip('/')}/tasks.task.chat.message.send.json"
    resp = await _get_client().post(
        url,
        json={"fields": {"taskId": task_id, "text": text}},
    )
    if not resp.is_success:
        raise RuntimeError(f"Bitrix tasks.task.chat.message.send {resp.status_code}: {resp.text}")
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Bitrix tasks.task.chat.message.send error: {data}")


async def get_task_chat_id(task_id: str) -> str | None:
    """Дотягивает DIALOG_ID чата задачи (tasks.task.get) для последующего чтения
    сообщений через im.dialog.messages.get — в новой карточке задачи комментарии
    физически хранятся в чате, отдельного chat_id в вебхуке ONTASKCOMMENTADD нет."""
    if not settings.bitrix_webhook_url:
        return None
    url = f"{settings.bitrix_webhook_url.rstrip('/')}/tasks.task.get.json"
    try:
        resp = await _get_client().post(
            url, json={"taskId": task_id, "select": ["ID", "CHAT_ID", "chat.id"]},
        )
    except httpx.RequestError:
        return None
    if not resp.is_success:
        _log.warning("Bitrix tasks.task.get %s: %s", resp.status_code, resp.text)
        return None
    data = resp.json()
    if "error" in data:
        _log.warning("Bitrix tasks.task.get error: %s", data)
        return None
    _log.info("Bitrix tasks.task.get сырой ответ: %s", data)
    task = (data.get("result") or {}).get("task") or {}
    chat = task.get("chat") if isinstance(task.get("chat"), dict) else {}
    chat_id = (
        task.get("chatId") or task.get("CHAT_ID")
        or chat.get("id") or chat.get("ID")
    )
    return f"chat{chat_id}" if chat_id else None


async def get_dialog_message(dialog_id: str, message_id: str) -> dict | None:
    """Дотягивает одно конкретное сообщение чата задачи (im.dialog.messages.get)
    по ID, который приходит в вебхуке ONTASKCOMMENTADD как MESSAGE_ID."""
    if not settings.bitrix_webhook_url:
        return None
    url = f"{settings.bitrix_webhook_url.rstrip('/')}/im.dialog.messages.get.json"
    try:
        target_id = int(message_id)
    except ValueError:
        return None
    try:
        resp = await _get_client().post(
            url, json={"DIALOG_ID": dialog_id, "FIRST_ID": target_id - 1, "LIMIT": 5},
        )
    except httpx.RequestError:
        return None
    if not resp.is_success:
        _log.warning("Bitrix im.dialog.messages.get %s: %s", resp.status_code, resp.text)
        return None
    data = resp.json()
    if "error" in data:
        _log.warning("Bitrix im.dialog.messages.get error: %s", data)
        return None
    messages = (data.get("result") or {}).get("messages") or []
    for m in messages:
        if str(m.get("id")) == str(message_id):
            return m
    return None


async def update_task_status(task_id: str, status: str) -> None:
    """Обновляет статус задачи в Bitrix24 (tasks.task.update), отражая изменение
    статуса заявки у нас (open/in_progress/closed). Обратное направление —
    изменение статуса прямо в Bitrix — подтягивается отдельным опросом,
    см. get_task_statuses и service_request_service.sync_statuses_from_bitrix."""
    if not settings.bitrix_webhook_url:
        return
    bitrix_status = _STATUS_TO_BITRIX.get(status)
    if bitrix_status is None:
        return

    url = f"{settings.bitrix_webhook_url.rstrip('/')}/tasks.task.update.json"
    resp = await _get_client().post(
        url,
        json={"taskId": task_id, "fields": {"STATUS": bitrix_status}},
    )
    if not resp.is_success:
        raise RuntimeError(f"Bitrix tasks.task.update {resp.status_code}: {resp.text}")
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Bitrix tasks.task.update error: {data}")


async def get_task_statuses(task_ids: list[str]) -> dict[str, str]:
    """Опрашивает Bitrix24 (tasks.task.list) за текущим STATUS пачки задач разом —
    используется для обратной синхронизации (кто-то поменял статус прямо в Bitrix,
    минуя приложение). Возвращает {task_id: STATUS}, пропуская не найденные задачи.
    Не бросает исключение при отсутствии настройки — просто отдаёт пустой словарь."""
    if not settings.bitrix_webhook_url or not task_ids:
        return {}

    url = f"{settings.bitrix_webhook_url.rstrip('/')}/tasks.task.list.json"
    resp = await _get_client().post(
        url,
        json={"filter": {"ID": task_ids}, "select": ["ID", "STATUS"]},
    )
    if not resp.is_success:
        raise RuntimeError(f"Bitrix tasks.task.list {resp.status_code}: {resp.text}")
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Bitrix tasks.task.list error: {data}")

    _log.info("Bitrix tasks.task.list сырой ответ: %s", data)
    result = data.get("result")
    tasks = result.get("tasks", []) if isinstance(result, dict) else (result or [])

    # Регистр полей у tasks.task.list не документирован надёжно (в этой сессии уже
    # дважды не совпадал с ожидаемым для других методов Bitrix) — берём и id/status, и ID/STATUS
    parsed = {}
    for t in tasks:
        task_id = t.get("id") or t.get("ID")
        status = t.get("status") or t.get("STATUS")
        if task_id is not None and status is not None:
            parsed[str(task_id)] = str(status)
    return parsed


async def _call(method: str, payload: dict | None = None) -> dict | list | None:
    """Один вызов REST-метода Bitrix. None — Bitrix не настроен, сеть отвалилась
    или метод вернул ошибку; вызывающий сам решает, что делать (обычно —
    пропустить обновление, а не ронять обработку вебхука)."""
    if not settings.bitrix_webhook_url:
        return None
    url = f"{settings.bitrix_webhook_url.rstrip('/')}/{method}.json"
    try:
        resp = await _get_client().post(url, json=payload or {})
    except httpx.RequestError as e:
        _log.warning("Bitrix %s: сетевая ошибка %s", method, e)
        return None
    if not resp.is_success:
        _log.warning("Bitrix %s: HTTP %s %s", method, resp.status_code, resp.text[:200])
        return None
    data = resp.json()
    if "error" in data:
        _log.warning("Bitrix %s: %s", method, data)
        return None
    return data.get("result")


async def get_deal(deal_id: str) -> dict | None:
    """Сделка CRM целиком (crm.deal.get) — событие ONCRMDEALADD/ONCRMDEALUPDATE
    присылает только ID, полей в самом вебхуке нет. Возвращает и стандартные поля
    (TITLE, COMPANY_ID, CONTACT_ID), и пользовательские UF_CRM_*. Требует, чтобы у
    входящего вебхука (BITRIX_WEBHOOK_URL) была включена область "CRM"."""
    result = await _call("crm.deal.get", {"ID": deal_id})
    return result if isinstance(result, dict) else None


async def get_company_name(company_id: str) -> str | None:
    """Название компании-заказчика (crm.company.get). У сделки есть только
    COMPANY_ID, само название — отдельным запросом."""
    if not company_id or str(company_id) in ("0", "None"):
        return None
    result = await _call("crm.company.get", {"ID": company_id})
    return (result or {}).get("TITLE") if isinstance(result, dict) else None


async def get_deal_contact_ids(deal_id: str) -> list[str] | None:
    """ID всех контактов сделки (crm.deal.contact.items.get) в порядке сортировки.
    У сделки может быть несколько контактных лиц — CONTACT_ID хранит только основное.

    None — запрос не удался, пустой список — контактов действительно нет. Разница
    важна: набор контактов синхронизируется с заменой, и при сбое нельзя принять
    "ничего не пришло" за "контактов больше нет" и стереть их."""
    result = await _call("crm.deal.contact.items.get", {"id": deal_id})
    if not isinstance(result, list):
        return None
    items = sorted(result, key=lambda r: int(r.get("SORT") or 0))
    return [str(r["CONTACT_ID"]) for r in items if r.get("CONTACT_ID")]


def _multifield_values(raw) -> list[str]:
    """Телефоны и почты в Bitrix — множественные поля: список словарей вида
    [{"VALUE": "+375...", "VALUE_TYPE": "WORK"}]. Достаём только значения."""
    if not isinstance(raw, list):
        return []
    return [str(item["VALUE"]) for item in raw if isinstance(item, dict) and item.get("VALUE")]


async def get_contact(contact_id: str) -> dict | None:
    """Контактное лицо (crm.contact.get) — ФИО, должность, телефоны, почты."""
    result = await _call("crm.contact.get", {"ID": contact_id})
    if not isinstance(result, dict):
        return None
    full_name = " ".join(
        part for part in (
            result.get("LAST_NAME"), result.get("NAME"), result.get("SECOND_NAME"),
        ) if part
    ).strip()
    return {
        "bitrix_contact_id": str(result.get("ID") or contact_id),
        "full_name": full_name or None,
        "post": result.get("POST") or None,
        "phones": _multifield_values(result.get("PHONE")),
        "emails": _multifield_values(result.get("EMAIL")),
    }


def _deal_select_fields() -> list[str]:
    """Поля сделки, нужные для карточки проекта. Пользовательские берём из
    настроек — их коды свои у каждого портала."""
    fields = ["ID", "TITLE", "COMPANY_ID", "CONTACT_ID"]
    for setting in (
        settings.bitrix_field_production_number,
        settings.bitrix_field_shipment_planned,
        settings.bitrix_field_shipment_actual,
    ):
        fields.extend(code.strip() for code in (setting or "").split(",") if code.strip())
    return fields


async def list_deals(start: int = 0) -> tuple[list[dict], int | None]:
    """Одна страница сделок CRM через crm.deal.list — для разового импорта уже
    существующих сделок (app/cli.py import-bitrix-deals). Возвращает (сделки, next):
    next передаётся в следующий вызов как start, None — сделок больше нет."""
    if not settings.bitrix_webhook_url:
        return [], None

    url = f"{settings.bitrix_webhook_url.rstrip('/')}/crm.deal.list.json"
    resp = await _get_client().post(
        url, json={"select": _deal_select_fields(), "start": start}
    )
    if not resp.is_success:
        raise RuntimeError(f"Bitrix crm.deal.list {resp.status_code}: {resp.text}")
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Bitrix crm.deal.list error: {data}")

    deals = [d for d in (data.get("result") or []) if "ID" in d]
    return deals, data.get("next")


async def get_user_name(bitrix_user_id: str) -> str | None:
    """Резолвит имя автора комментария в Bitrix (user.get) — best-effort,
    используется только для подписи пересланного в приложение сообщения."""
    if not settings.bitrix_webhook_url:
        return None
    url = f"{settings.bitrix_webhook_url.rstrip('/')}/user.get.json"
    try:
        resp = await _get_client().get(url, params={"ID": bitrix_user_id})
        if not resp.is_success:
            return None
        users = resp.json().get("result") or []
        if not users:
            return None
        name = " ".join(p for p in (users[0].get("NAME"), users[0].get("LAST_NAME")) if p)
        return name or None
    except Exception:
        return None


async def ensure_bitrix_user(session: AsyncSession) -> int:
    """Системный пользователь, от лица которого в чат заявки попадают сообщения,
    пересланные из комментариев Bitrix-задачи (см. bitrix_webhook_service.py).
    Роль 'operator' — чтобы ChatService пропускал его в любой чат заявки."""
    import secrets
    from sqlalchemy import select
    from app.core.security import hash_password
    from app.models.role import Role
    from app.models.user import User

    role = (await session.execute(
        select(Role).where(Role.name == "operator")
    )).scalar_one_or_none()
    if role is None:
        raise RuntimeError("Роль 'operator' не найдена в БД — примените миграции")

    result = await session.execute(select(User).where(User.login == _INCOMING_USER_LOGIN))
    user = result.scalar_one_or_none()
    if user:
        if user.full_name != _INCOMING_USER_NAME:
            user.full_name = _INCOMING_USER_NAME
            await session.commit()
        return user.id

    user = User(
        login=_INCOMING_USER_LOGIN,
        full_name=_INCOMING_USER_NAME,
        hashed_password=hash_password(secrets.token_hex(32)),
        role_id=role.id,
        is_phone_verified=True,
        is_active=True,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user.id
