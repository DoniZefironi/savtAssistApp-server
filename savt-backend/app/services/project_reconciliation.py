from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat
from app.repositories.cabinet import CabinetRepository
from app.repositories.chat import ChatRepository


async def ensure_cabinet_chats(
    session: AsyncSession, user_ids: list[int], cabinet_ids: list[int],
) -> list[Chat]:
    """Заводит личный чат ШУ для каждой пары (пользователь, шкаф), если его ещё
    нет — не дожидаясь первого открытия чата пользователем (ChatService.get_cabinet_chat
    тоже умеет создавать лениво, это просто раньше по времени).

    Доступ к шкафам сам по себе НИГДЕ не материализуется: в проектной модели он
    всегда вычисляется — состоит ли пользователь в user_projects того проекта,
    которому принадлежит шкаф (см. CabinetRepository.user_has_access). Эта
    функция только про чаты, не про права.

    Не коммитит сессию и не публикует realtime-события — ответственность
    вызывающего кода (после commit), возвращает список вновь созданных чатов
    для публикации.
    """
    if not user_ids or not cabinet_ids:
        return []

    chat_repo = ChatRepository(session)
    from app.services.chat_service import ChatService
    chat_service = ChatService(session)

    created_chats: list[Chat] = []
    for cabinet_id in cabinet_ids:
        for user_id in user_ids:
            if await chat_repo.find(user_id, "cabinet", cabinet_id) is not None:
                continue
            created_chats.append(await chat_service.ensure_cabinet_chat(user_id, cabinet_id))
    return created_chats


async def ensure_cabinet_chats_for_project(
    session: AsyncSession, project_id: int, user_ids: list[int],
) -> list[Chat]:
    """Тот же ensure_cabinet_chats, но для всех шкафов проекта разом — вызывается,
    когда меняется состав участников проекта или список его шкафов (вступление в
    проект, привязка нового ШУ к проекту и т.п.)."""
    cabinets = await CabinetRepository(session).list_by_project(project_id)
    return await ensure_cabinet_chats(session, user_ids, [c.id for c in cabinets])
