"""Право на поток событий проверяется по базе, а не только по билету. Билет говорит, кем был
вошедший пять минут назад, а поток живёт часами (пинг поддерживает его бесконечно), поэтому
пользователь, которого заблокировали, удалили или разжаловали из оператора, не должен
получать события дальше. Проверка идёт при подключении и раз в RECHECK_SECONDS внутри потока."""
from app.core.constants import STAFF_ROLE_NAMES
from app.core.stream_tickets import SCOPE_OPERATOR

RECHECK_SECONDS = 60


async def may_stream(user_id: int, scope: str) -> bool:
    """Пользователь существует и активен, а для операторских потоков он всё ещё оператор,
    администратор или суперадмин."""
    from app.database import AsyncSessionLocal
    from app.models.role import Role
    from app.repositories.user import UserRepository

    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).get_by_id(user_id)
        if user is None or not user.is_active:
            return False
        if scope != SCOPE_OPERATOR:
            return True
        role = await session.get(Role, user.role_id)
        return role is not None and role.name in STAFF_ROLE_NAMES
