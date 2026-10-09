"""Общие шаги решения по заявке (регистрация, сброс пароля, смена номера, добавление
шкафа, доступ к документу): проверка, что заявка ещё ждёт решения, и отметка о том,
кто и когда её решил."""
from datetime import datetime, timezone

from app.core.exceptions import AlreadyExistsError


def ensure_pending(req) -> None:
    """Решение принимается один раз: повторное одобрение или отказ — ошибка."""
    if req.status != "pending":
        raise AlreadyExistsError("Заявка уже обработана")


def resolve_request(req, status: str, admin_response: str | None, admin_id: int) -> None:
    """Фиксирует решение: итоговый статус, ответ администратора, автора и время."""
    req.status = status
    req.admin_response = admin_response
    req.resolved_by_admin_id = admin_id
    req.resolved_at = datetime.now(timezone.utc)
