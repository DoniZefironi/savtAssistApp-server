from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.cabinet import CabinetRepository, UserCabinetRepository
from app.repositories.project import ProjectRepository, ProjectRequestRepository, UserProjectRepository
from app.schemas.project import ProjectCabinetItem, UserProjectDetailOut, UserProjectListItemOut
from app.services.project_reconciliation import reconcile_cabinet_access


class UserProjectService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.project_repo = ProjectRepository(session)
        self.cabinet_repo = CabinetRepository(session)
        self.user_cabinet_repo = UserCabinetRepository(session)
        self.user_project_repo = UserProjectRepository(session)
        self.request_repo = ProjectRequestRepository(session)

    # Список проектов пользователя
    async def list_projects(self, user_id: int) -> list[UserProjectListItemOut]:
        rows = await self.user_project_repo.list_for_user(user_id)
        items = []
        for up, project in rows:
            cabinets = await self.cabinet_repo.list_by_project(project.id)
            items.append(UserProjectListItemOut(
                project_id=project.id, name=project.name, is_primary=up.is_primary,
                cabinet_count=len(cabinets),
            ))
        return items

    # Подробнее о проекте — показываем только те шкафы проекта, к которым
    # у пользователя реально есть доступ (не все шкафы проекта вообще)
    async def get_project(self, user_id: int, project_id: int) -> UserProjectDetailOut:
        row = await self.user_project_repo.get_with_project(user_id, project_id)
        if row is None:
            raise NotFoundError("Проект не найден")
        up, project = row

        all_cabinets = await self.cabinet_repo.list_by_project(project.id)
        accessible = [c for c in all_cabinets if await self.user_cabinet_repo.find(user_id, c.id) is not None]

        return UserProjectDetailOut(
            project_id=project.id,
            name=project.name,
            is_primary=up.is_primary,
            cabinets=[
                ProjectCabinetItem(
                    id=c.id, type=c.type, object_number=c.object_number, admin_internal_name=c.admin_internal_name,
                )
                for c in accessible
            ],
        )

    # Добавление проекта по кур-коду
    async def add_by_qr(self, user_id: int, unique_code: str) -> dict:
        project = await self.project_repo.find_by_code(unique_code)
        if project is None:
            raise NotFoundError("Проект с таким кодом не найден")

        existing = await self.user_project_repo.find(user_id, project.id)
        if existing is not None:
            raise AlreadyExistsError("Этот проект уже привязан к вашему аккаунту")

        has_primary = await self.user_project_repo.has_primary(project.id)

        if not has_primary:
            await self.user_project_repo.create(user_id=user_id, project_id=project.id, is_primary=True)
            # свежий проект: шкафы, которых у юзера ещё нет, привязываются напрямую,
            # а на занятые посторонним — заявка на конкретный шкаф (bypass_request=False)
            created_chats = await reconcile_cabinet_access(
                self.session, project.id, [user_id], bypass_request=False,
            )
            await self.session.commit()
            if created_chats:
                from app.services.chat_service import chat_summary_dict
                from app.services.realtime_events import publish_chat_created
                for chat in created_chats:
                    await publish_chat_created(chat.id, chat_summary_dict(chat))
            return {"status": "linked", "message": "Проект успешно привязан"}

        pending = await self.request_repo.find_pending_share(user_id, project.id)
        if pending is not None:
            raise AlreadyExistsError("Заявка на доступ к этому проекту уже отправлена")

        await self.request_repo.create_share(user_id=user_id, project_id=project.id)
        await self.session.commit()
        return {"status": "request_submitted", "message": "Заявка отправлена администратору на рассмотрение"}
