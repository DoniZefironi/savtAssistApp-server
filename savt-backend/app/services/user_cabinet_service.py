from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.models.chat import Chat
from app.models.message import Message
from app.repositories.cabinet import CabinetRepository, CabinetRequestRepository, CabinetUserSettingsRepository
from app.repositories.project import ProjectRepository, UserProjectRepository
from app.schemas.cabinet import UserCabinetDetailOut, UserCabinetListItemOut, UserCabinetPatchIn
from app.utils.warranty import warranty_status as _warranty_status


class UserCabinetService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cabinet_repo = CabinetRepository(session)
        self.project_repo = ProjectRepository(session)
        self.user_project_repo = UserProjectRepository(session)
        self.settings_repo = CabinetUserSettingsRepository(session)
        self.request_repo = CabinetRequestRepository(session)

    # Список ШУ, доступных пользователю — все шкафы проектов, в которых он
    # состоит (доступ выводится из проекта, см. CabinetRepository.list_accessible_for_user)
    async def list_cabinets(self, user_id: int) -> list[UserCabinetListItemOut]:
        cabinets = await self.cabinet_repo.list_accessible_for_user(user_id)
        if not cabinets:
            return []

        cabinet_ids = [c.id for c in cabinets]
        unread = await self._get_unread_counts(user_id, cabinet_ids)
        settings_map = await self.settings_repo.get_map_for_user(user_id, cabinet_ids)
        project_ids = list({c.project_id for c in cabinets if c.project_id is not None})
        project_names = await self.project_repo.get_names_by_ids(project_ids)

        return [
            UserCabinetListItemOut(
                cabinet_id=cab.id,
                type=cab.type,
                object_number=cab.object_number,
                warranty_ends_at=cab.warranty_ends_at,
                warranty_status=_warranty_status(cab.warranty_ends_at),
                custom_name=_display_name(settings_map.get(cab.id), cab),
                unread_count=unread.get(cab.id, 0),
                project_id=cab.project_id,
                project_name=project_names.get(cab.project_id) if cab.project_id is not None else None,
            )
            for cab in cabinets
        ]

    # Получение ШУ (одного)
    async def get_cabinet(self, user_id: int, cabinet_id: int) -> UserCabinetDetailOut:
        cab = await self.cabinet_repo.get_accessible_for_user(user_id, cabinet_id)
        if cab is None:
            raise NotFoundError("ШУ не найден")
        settings = await self.settings_repo.get(user_id, cabinet_id)
        project_names = await self.project_repo.get_names_by_ids(
            [cab.project_id] if cab.project_id is not None else []
        )
        return UserCabinetDetailOut(
            cabinet_id=cab.id,
            type=cab.type,
            object_number=cab.object_number,
            description=cab.description,
            purpose=cab.purpose,
            warranty_starts_at=cab.warranty_starts_at,
            warranty_ends_at=cab.warranty_ends_at,
            warranty_status=_warranty_status(cab.warranty_ends_at),
            latitude=cab.latitude,
            longitude=cab.longitude,
            custom_name=_display_name(settings, cab),
            custom_comment=settings.custom_comment if settings else None,
            project_id=cab.project_id,
            project_name=project_names.get(cab.project_id) if cab.project_id is not None else None,
        )

    # Обновление личной персонализации ШУ (имя/заметка) — доступ к ШУ, а не
    # владение им: раз шкаф виден пользователю, он может его переименовать у себя
    async def update_cabinet(
        self, user_id: int, cabinet_id: int, data: UserCabinetPatchIn
    ) -> UserCabinetDetailOut:
        if not await self.cabinet_repo.user_has_access(user_id, cabinet_id):
            raise NotFoundError("ШУ не найден")
        await self.settings_repo.upsert(user_id, cabinet_id, data.model_dump(exclude_unset=True))
        await self.session.commit()
        return await self.get_cabinet(user_id, cabinet_id)

    # Добавление ШУ по фото — в проектной модели это "в моём проекте не хватает
    # шкафа", а не "дайте мне доступ к неизвестному ШУ" (см. AddByPhotoIn.project_id)
    async def add_by_photo(
        self, user_id: int, project_id: int, photo_url: str, user_comment: str | None
    ) -> int:
        if await self.user_project_repo.find(user_id, project_id) is None:
            raise PermissionDeniedError("Вы не состоите в этом проекте")

        pending = await self.request_repo.find_pending_addition(user_id)
        if pending is not None:
            raise AlreadyExistsError("У вас уже есть необработанная заявка на добавление ШУ")

        request = await self.request_repo.create_addition(
            user_id=user_id,
            project_id=project_id,
            photo_url=photo_url,
            user_comment=user_comment,
        )
        await self.session.commit()
        return request.id

    # Получение кол-ва непрочитанных сообщений в чате ШУ
    async def _get_unread_counts(self, user_id: int, cabinet_ids: list[int]) -> dict[int, int]:
        result = await self.session.execute(
            select(Chat.cabinet_id, func.count(Message.id))
            .join(Message, Message.chat_id == Chat.id)
            .where(
                Chat.user_id == user_id,
                Chat.cabinet_id.in_(cabinet_ids),
                Message.is_read == False,
                Message.sender_id != user_id,
                Message.deleted_at.is_(None),
            )
            .group_by(Chat.cabinet_id)
        )
        return {cabinet_id: count for cabinet_id, count in result.all()}


def _display_name(settings, cabinet) -> str | None:
    if settings is not None and settings.custom_name:
        return settings.custom_name
    return cabinet.admin_internal_name or cabinet.object_number
