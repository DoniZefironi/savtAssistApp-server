"""Общие шаги решения по заявке (регистрация, сброс пароля, смена номера, добавление
шкафа, доступ к документу): проверка, что заявка ещё ждёт решения, и отметка о том,
кто и когда её решил."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError


async def next_fake_request_id(session: AsyncSession, model) -> int:
    """Номер из той же последовательности, что и у настоящих заявок, — для ответа на подачу,
    когда заявка не создаётся (номер уже зарегистрирован, заявка уже есть, пользователя нет).
    Ответ подачи без авторизации обязан выглядеть одинаково во всех случаях, иначе по нему
    можно перебирать номера телефонов. Номер каждый раз новый, как у настоящей заявки."""
    sequence = await session.scalar(select(func.pg_get_serial_sequence(model.__tablename__, "id")))
    return await session.scalar(select(func.nextval(sequence)))


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
