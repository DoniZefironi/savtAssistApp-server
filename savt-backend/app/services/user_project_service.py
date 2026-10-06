from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.cabinet import CabinetRepository, UserCabinetRepository
from app.repositories.chat import ChatRepository
from app.repositories.project import ProjectRepository, UserProjectRepository
from app.schemas.project import ProjectCabinetItem, UserProjectDetailOut, UserProjectListItemOut
from app.utils.warranty import warranty_status as _warranty_status


class UserProjectService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.project_repo = ProjectRepository(session)
        self.cabinet_repo = CabinetRepository(session)
        self.user_project_repo = UserProjectRepository(session)
        self.user_cabinet_repo = UserCabinetRepository(session)
        self.chat_repo = ChatRepository(session)

    # Список проектов пользователя. Кол-во ШУ — одним батч-запросом на все
    # проекты разом (count_by_projects), не по одному в цикле — раньше это
    # был N+1: список из 10 проектов означал 11 запросов вместо 2
    async def list_projects(self, user_id: int) -> list[UserProjectListItemOut]:
        rows = await self.user_project_repo.list_for_user(user_id)
        cabinet_counts = await self.cabinet_repo.count_by_projects([project.id for _up, project in rows])
        return [
            UserProjectListItemOut(
                project_id=project.id, name=project.name,
                is_pinned=up.is_pinned,
                cabinet_count=cabinet_counts.get(project.id, 0),
                company_name=project.company_name,
                warranty_status=_warranty_status(project.warranty_ends_at),
            )
            for up, project in rows
        ]

    # Подробнее о проекте — все шкафы проекта, доступ к ним у участника
    # одинаковый и не выбирается по-шкафно (см. общую идею проектного доступа)
    async def get_project(self, user_id: int, project_id: int) -> UserProjectDetailOut:
        row = await self.user_project_repo.get_with_project(user_id, project_id)
        if row is None:
            raise NotFoundError("Проект не найден")
        up, project = row

        cabinets = await self.cabinet_repo.list_by_project(project.id)

        return UserProjectDetailOut(
            project_id=project.id,
            name=project.name,
            is_pinned=up.is_pinned,
            cabinets=[
                ProjectCabinetItem(
                    id=c.id, type=c.type, object_number=c.object_number, admin_internal_name=c.admin_internal_name,
                )
                for c in cabinets
            ],
            # Контактных лиц заказчика здесь намеренно нет — только сотрудникам
            company_name=project.company_name,
            shipment_planned_at=project.shipment_planned_at,
            shipment_actual_at=project.shipment_actual_at,
            warranty_starts_at=project.warranty_starts_at,
            warranty_ends_at=project.warranty_ends_at,
            warranty_status=_warranty_status(project.warranty_ends_at),
        )

    # Добавление проекта по кур-коду — сразу, без чьего-либо одобрения:
    # сколько угодно пользователей может состоять в одном проекте, без
    # очередности и без "главного" участника
    async def add_by_qr(self, user_id: int, unique_code: str) -> dict:
        project = await self.project_repo.find_by_code(unique_code)
        if project is None:
            raise NotFoundError("Проект с таким кодом не найден")

        existing = await self.user_project_repo.find(user_id, project.id)
        if existing is not None:
            raise AlreadyExistsError("Этот проект уже привязан к вашему аккаунту")

        await self.user_project_repo.create(user_id=user_id, project_id=project.id)

        # Слияние: ШУ этого проекта, которыми пользователь уже владел напрямую
        # (UserCabinet, добавлены отдельно по своему QR) — прямая привязка
        # убирается, доступ дальше идёт через только что созданное членство.
        # Чат и история по ШУ (заявки, рекламации) не переносятся и не
        # трогаются вовсе: они уже привязаны к cabinet_id, который не меняется.
        project_cabinet_ids = [c.id for c in await self.cabinet_repo.list_by_project(project.id)]
        merged = await self.user_cabinet_repo.list_for_user_in_cabinets(user_id, project_cabinet_ids)
        merged_cabinet_ids = [uc.cabinet_id for uc in merged]
        for uc in merged:
            await self.user_cabinet_repo.delete(uc)

        # Доступ ко всем шкафам проекта уже есть самим членством выше. Чат
        # ШУ никогда не создаётся автоматически — только сам пользователь,
        # открыв ШУ и нажав на чат (см. ChatService.get_cabinet_chat).
        # Чат самого проекта заводим сразу, не дожидаясь первого открытия.
        had_chat = await self.chat_repo.find(user_id, "project", project_id=project.id) is not None
        from app.services.chat_service import ChatService
        project_chat = await ChatService(self.session).ensure_project_chat(user_id, project.id)
        await self.session.commit()
        if not had_chat:
            from app.services.chat_service import chat_summary_dict
            from app.services.realtime_events import publish_chat_created
            await publish_chat_created(project_chat.id, chat_summary_dict(project_chat))

        if merged_cabinet_ids:
            from app.services.notification_service import NotificationService
            notif_service = NotificationService(self.session)
            for cabinet_id in merged_cabinet_ids:
                await notif_service.send(
                    user_id=user_id, type_="request_status",
                    title="ШУ перенесён в проект",
                    body=f"Теперь доступен через проект «{project.name}»",
                    data={"cabinet_id": cabinet_id, "project_id": project.id},
                )
        return {"status": "linked", "message": "Проект успешно привязан"}

    # Закрепить/открепить проект наверх списка GET /projects. Закреп живёт на
    # самой связи UserProject — открепляется сам, если пользователь потом
    # покинет проект, отдельно чистить не нужно.
    async def pin_project(self, user_id: int, project_id: int) -> None:
        up = await self.user_project_repo.find(user_id, project_id)
        if up is None:
            raise NotFoundError("Проект не найден")
        await self.user_project_repo.set_pinned(up, True)
        await self.session.commit()

    async def unpin_project(self, user_id: int, project_id: int) -> None:
        up = await self.user_project_repo.find(user_id, project_id)
        if up is None:
            raise NotFoundError("Проект не найден")
        await self.user_project_repo.set_pinned(up, False)
        await self.session.commit()

    # Пользователь сам покидает проект — теряет доступ разом ко всем его
    # шкафам (доступ выводится из членства, точечно выйти из одного ШУ нельзя,
    # см. общую идею проектного доступа). Заодно архивирует его чаты по этому
    # проекту и его шкафам (и заявкам) — та же причина, что и у
    # ProjectService.remove_user_from_project (симметричное действие).
    async def leave_project(self, user_id: int, project_id: int) -> None:
        up = await self.user_project_repo.find(user_id, project_id)
        if up is None:
            raise NotFoundError("Проект не найден")
        await self.user_project_repo.delete(up)

        cabinet_ids = [c.id for c in await self.cabinet_repo.list_by_project(project_id)]
        from app.services.chat_service import ChatService
        archived_chats = await ChatService(self.session).archive_user_project_chats(user_id, project_id, cabinet_ids)

        await self.session.commit()

        if archived_chats:
            from app.services.chat_service import chat_summary_dict
            from app.services.realtime_events import publish_chat_updated
            for chat in archived_chats:
                await publish_chat_updated(chat.id, chat_summary_dict(chat))
