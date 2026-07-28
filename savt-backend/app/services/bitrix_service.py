import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

_client: httpx.AsyncClient | None = None

_INCOMING_USER_LOGIN = "__bitrix__"
_INCOMING_USER_NAME = "Bitrix"

# Статусы задач Bitrix24 (поле STATUS в tasks.task.*): 1 Новая, 2 Ждёт выполнения,
# 3 Выполняется, 4 Ждёт контроля, 5 Завершена, 6 Отложена, 7 Отклонена
_STATUS_TO_BITRIX = {
    "open": "2",
    "in_progress": "3",
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
    """Добавляет комментарий в ленту задачи Bitrix24 (task.commentitem.add) —
    используется для синхронизации сообщений заявителя из чата заявки в задачу."""
    if not settings.bitrix_webhook_url:
        return

    url = f"{settings.bitrix_webhook_url.rstrip('/')}/task.commentitem.add.json"
    resp = await _get_client().post(
        url,
        json={"taskId": task_id, "fields": {"POST_MESSAGE": text}},
    )
    if not resp.is_success:
        raise RuntimeError(f"Bitrix task.commentitem.add {resp.status_code}: {resp.text}")
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Bitrix task.commentitem.add error: {data}")


async def update_task_status(task_id: str, status: str) -> None:
    """Обновляет статус задачи в Bitrix24 (tasks.task.update), отражая изменение
    статуса заявки у нас (open/in_progress/closed). Одностороннее — изменение
    статуса задачи прямо в Bitrix обратно к нам не подтягивается."""
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
