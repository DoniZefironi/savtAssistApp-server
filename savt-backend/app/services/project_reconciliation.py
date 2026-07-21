from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat
from app.repositories.cabinet import CabinetRepository, CabinetRequestRepository, UserCabinetRepository


async def reconcile_cabinet_access(
    session: AsyncSession, project_id: int, user_ids: list[int], bypass_request: bool
) -> list[Chat]:
    """Разбирает шкафы проекта по пользователям из user_ids (участник с is_primary=True
    в проекте должен идти первым в списке, чтобы при прочих равных именно он становился
    primary-владельцем ранее ничейного шкафа).

    Для каждого шкафа проекта и каждого user_id:
    - у пользователя этот шкаф уже есть -> пропуск (дедупликация, ничего не меняем);
    - шкаф ничей -> прямая привязка, is_primary=True;
    - шкаф уже занят другим пользователем:
        - bypass_request=True (действие инициировал админ — привязка шкафа к проекту
          или одобрение заявки на проект) -> тоже прямая привязка, is_primary=False,
          без дополнительных заявок;
        - bypass_request=False (пользователь сам сканирует QR проекта) -> точечная
          CabinetShareRequest на этот один шкаф, требует апрува админом.

    Не коммитит сессию и не публикует realtime-события — это ответственность вызывающего
    кода (после commit), возвращает список вновь созданных чатов для публикации.
    """
    cabinet_repo = CabinetRepository(session)
    user_cabinet_repo = UserCabinetRepository(session)
    request_repo = CabinetRequestRepository(session)

    cabinets = await cabinet_repo.list_by_project(project_id)
    if not cabinets:
        return []

    from app.services.chat_service import ChatService
    chat_service = ChatService(session)

    created_chats: list[Chat] = []

    for cabinet in cabinets:
        for user_id in user_ids:
            existing = await user_cabinet_repo.find(user_id, cabinet.id)
            if existing is not None:
                continue

            has_primary = await user_cabinet_repo.has_primary(cabinet.id)
            if not has_primary:
                await user_cabinet_repo.create(user_id=user_id, cabinet_id=cabinet.id, is_primary=True)
            elif bypass_request:
                await user_cabinet_repo.create(user_id=user_id, cabinet_id=cabinet.id, is_primary=False)
            else:
                pending = await request_repo.find_pending_share(user_id, cabinet.id)
                if pending is None:
                    await request_repo.create_share(user_id=user_id, cabinet_id=cabinet.id)
                continue

            chat = await chat_service.ensure_cabinet_chat(user_id, cabinet.id)
            created_chats.append(chat)

    return created_chats
