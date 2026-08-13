from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.cabinets import Cabinet
from app.models.chat import Chat
from app.repositories.cabinet import CabinetRepository
from app.repositories.project import ProjectRepository
from app.repositories.tag import TagRepository
from app.schemas.cabinet import CabinetCreateIn, CabinetGeoItem, CabinetListOut, CabinetOut, CabinetUpdateIn
from app.schemas.tags import TagOut
from app.schemas.pagination import PageOut, make_page
from app.services.audit_service import AuditLogger
from app.utils.warranty import warranty_status as _warranty_status


async def _resolve_type(session: AsyncSession, raw_type: str) -> str:
    """Нормализует тип ШУ в нижний регистр и создаёт тег cabinet_type если его нет."""
    normalized = raw_type.strip().lower()
    if not normalized:
        return raw_type
    repo = TagRepository(session)
    existing = await repo.get_by_name_and_scope(normalized, "cabinet_type")
    if existing is None:
        await repo.create(normalized, "cabinet_type")
    return normalized


class CabinetService:
    def __init__(self, session: AsyncSession):
        self.repo = CabinetRepository(session)
        self.project_repo = ProjectRepository(session)
        self.session = session
        self.audit = AuditLogger(session)

    # Создание ШУ — только внутри существующего проекта (см. CabinetCreateIn.project_id).
    # Доступ участникам проекта не выдаётся отдельно — он и так есть самим
    # членством в проекте; здесь только заводится папка на NAS сразу же, не
    # дожидаясь первого обращения. Чат ШУ никогда не создаётся автоматически —
    # только сам пользователь, открыв ШУ и нажав на чат (см. ChatService.get_cabinet_chat).
    async def create(self, data: CabinetCreateIn, actor_id: int, actor_role: str) -> CabinetOut:
        project = await self.project_repo.get_by_id(data.project_id)
        if project is None or project.deleted_at is not None:
            raise NotFoundError("Проект не найден")

        cabinet = await self.repo.create(
            project_id=data.project_id,
            type=await _resolve_type(self.session, data.type),
            object_number=data.object_number,
            description=data.description,
            warranty_starts_at=data.warranty_starts_at,
            warranty_ends_at=data.warranty_ends_at,
            admin_internal_name=data.admin_internal_name,
            admin_comment=data.admin_comment,
            purpose=data.purpose,
            latitude=data.latitude,
            longitude=data.longitude,
        )
        await self.session.flush()
        self.audit.log("cabinet.create", "cabinet", cabinet.id, actor_id, actor_role,
                       {"object_number": cabinet.object_number, "type": cabinet.type})

        await self.session.commit()

        from app.services import project_folder_service
        project_folder_service.schedule_cabinet_folder(cabinet.id)

        from app.services.realtime_events import publish_cabinet_created
        members = await self.repo.list_users_with_access(cabinet.id)
        await publish_cabinet_created(cabinet.id, data.project_id, [u.id for u, _ in members])

        return await self.get(cabinet.id)

    # Получение ШУ с тегами
    async def get(self, cabinet_id: int) -> CabinetOut:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")
        tags_map = await self.repo.get_tags([cabinet_id])
        project_names = await self.project_repo.get_names_by_ids(
            [cabinet.project_id] if cabinet.project_id is not None else []
        )
        return CabinetOut(
            id=cabinet.id,
            type=cabinet.type,
            object_number=cabinet.object_number,
            description=cabinet.description,
            warranty_starts_at=cabinet.warranty_starts_at,
            warranty_ends_at=cabinet.warranty_ends_at,
            admin_internal_name=cabinet.admin_internal_name,
            admin_comment=cabinet.admin_comment,
            purpose=cabinet.purpose,
            latitude=cabinet.latitude,
            longitude=cabinet.longitude,
            mqtt_topic=cabinet.mqtt_topic,
            tags=[TagOut.model_validate(t) for t in tags_map.get(cabinet_id, [])],
            project_id=cabinet.project_id,
            project_name=project_names.get(cabinet.project_id) if cabinet.project_id is not None else None,
            created_at=cabinet.created_at,
            updated_at=cabinet.updated_at,
        )

    # Обновление ШУ
    async def update(self, cabinet_id: int, data: CabinetUpdateIn, actor_id: int, actor_role: str) -> CabinetOut:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")
        changed = data.model_dump(exclude_unset=True)
        if "type" in changed and changed["type"]:
            changed["type"] = await _resolve_type(self.session, changed["type"])
        if changed.get("mqtt_topic"):
            existing = await self.repo.get_by_mqtt_topic(changed["mqtt_topic"])
            if existing is not None and existing.id != cabinet_id:
                raise AlreadyExistsError(
                    f"Топик '{changed['mqtt_topic']}' уже привязан к другому ШУ (id={existing.id})"
                )
        for field, value in changed.items():
            setattr(cabinet, field, value)
        self.audit.log("cabinet.update", "cabinet", cabinet_id, actor_id, actor_role, {"fields": list(changed.keys())})
        await self.session.commit()
        await self.session.refresh(cabinet)
        return await self.get(cabinet_id)

    # Soft-delete одного ШУ + архивация его чатов (и чатов его заявок) — общая
    # часть для delete() и каскада delete_for_project(). Не коммитит и не
    # публикует события — это делает вызывающий код, вместе с остальными
    # изменениями транзакции.
    async def _soft_delete_and_archive(self, cabinet: Cabinet, actor_id: int, actor_role: str) -> list[Chat]:
        self.audit.log("cabinet.delete", "cabinet", cabinet.id, actor_id, actor_role,
                       {"object_number": cabinet.object_number})
        await self.repo.soft_delete(cabinet)
        from app.services.chat_service import ChatService
        return await ChatService(self.session).archive_cabinet_chats(cabinet.id)

    # Удаление ШУ (soft-delete: запись остаётся в БД, но перестаёт быть
    # доступна для поиска, привязки и повторного использования кода).
    # Заодно архивирует чаты этого ШУ у всех, у кого они есть — иначе
    # выглядело бы, будто с удалённым шкафом всё ещё можно переписываться.
    async def delete(self, cabinet_id: int, actor_id: int, actor_role: str) -> None:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None or cabinet.deleted_at is not None:
            raise NotFoundError("ШУ не найден")
        archived_chats = await self._soft_delete_and_archive(cabinet, actor_id, actor_role)

        await self.session.commit()

        if archived_chats:
            from app.services.chat_service import chat_summary_dict
            from app.services.realtime_events import publish_chat_updated
            for chat in archived_chats:
                await publish_chat_updated(chat.id, chat_summary_dict(chat))

    # Каскад при удалении проекта (см. ProjectService.delete): все ещё живые
    # ШУ этого проекта удаляются вместе с ним, тем же способом, что и
    # поштучное удаление — каждый получает свою запись в аудите. Не коммитит
    # и не публикует — вызывающий код собирает архивированные чаты со всего
    # проекта разом и коммитит одной транзакцией.
    async def delete_for_project(self, project_id: int, actor_id: int, actor_role: str) -> list[Chat]:
        cabinets = await self.repo.list_by_project(project_id)
        archived_chats: list[Chat] = []
        for cabinet in cabinets:
            archived_chats += await self._soft_delete_and_archive(cabinet, actor_id, actor_role)
        return archived_chats

    # Все ШУ
    async def list_all(
        self,
        query: str | None = None,
        tag_ids: list[int] | None = None,
        has_documents: bool | None = None,
        has_photos: bool | None = None,
        has_users: bool | None = None,
        has_service_requests: bool | None = None,
        warranty_status: str | None = None,
        project_id: int | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> PageOut[CabinetListOut]:
        offset = (page - 1) * size
        cabinets, total = await self.repo.search(
            query=query, tag_ids=tag_ids,
            has_documents=has_documents, has_photos=has_photos,
            has_users=has_users, has_service_requests=has_service_requests,
            warranty_status=warranty_status, project_id=project_id,
            sort_by=sort_by, sort_order=sort_order,
            offset=offset, limit=size,
        )
        cabinet_ids = [c.id for c in cabinets]
        tags_map = await self.repo.get_tags(cabinet_ids)
        project_ids = list({c.project_id for c in cabinets if c.project_id is not None})
        project_names = await self.project_repo.get_names_by_ids(project_ids)
        items = [
            CabinetListOut(
                id=c.id,
                type=c.type,
                object_number=c.object_number,
                purpose=c.purpose,
                warranty_starts_at=c.warranty_starts_at,
                warranty_ends_at=c.warranty_ends_at,
                warranty_status=_warranty_status(c.warranty_ends_at),
                admin_internal_name=c.admin_internal_name,
                admin_comment=c.admin_comment,
                tags=[TagOut.model_validate(t) for t in tags_map.get(c.id, [])],
                project_id=c.project_id,
                project_name=project_names.get(c.project_id) if c.project_id is not None else None,
                created_at=c.created_at,
            )
            for c in cabinets
        ]
        return make_page(items, total, page, size)

    # Привязать теги к ШУ
    async def set_tags(self, cabinet_id: int, tag_ids: list[int], actor_id: int, actor_role: str) -> None:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")
        await self.repo.set_tags(cabinet_id, tag_ids)
        self.audit.log("cabinet.set_tags", "cabinet", cabinet_id, actor_id, actor_role, {"tag_ids": tag_ids})
        await self.session.commit()

    async def get_geo(
        self, warranty_status: str | None = None, has_open_requests: bool | None = None,
    ) -> list[CabinetGeoItem]:
        rows = await self.repo.get_geo(warranty_status=warranty_status, has_open_requests=has_open_requests)
        return [
            CabinetGeoItem(
                id=row.id,
                object_number=row.object_number,
                admin_internal_name=row.admin_internal_name,
                warranty_status=_warranty_status(row.warranty_ends_at),
                latitude=row.latitude,
                longitude=row.longitude,
                has_open_requests=row.has_open_requests,
            )
            for row in rows
        ]
