from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat
from app.models.chat_pinned_message import ChatPinnedMessage
from app.models.chat_user_settings import ChatUserSettings
from app.models.message import Message
from app.models.message_attchment import MessageAttachment
from app.models.message_reaction import MessageReaction
from app.models.user import User
from app.utils.db import escape_like

# Типы чатов, видимые в общих списках/поиске оператора (личные заметки "notes"
# сюда никогда не входят - это приватное пространство самого пользователя)
VISIBLE_CHAT_TYPES = ("cabinet", "support", "service_request", "project")


class ChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, chat_id: int) -> Chat | None:
        return await self.session.get(Chat, chat_id)

    async def find(
        self, user_id: int, chat_type: str,
        cabinet_id: int | None = None, project_id: int | None = None,
    ) -> Chat | None:
        stmt = select(Chat).where(
            Chat.user_id == user_id,
            Chat.chat_type == chat_type,
        )
        if project_id is not None:
            stmt = stmt.where(Chat.project_id == project_id)
        elif cabinet_id is not None:
            stmt = stmt.where(Chat.cabinet_id == cabinet_id)
        else:
            # support и notes — по одному на пользователя, без привязки к объекту
            stmt = stmt.where(Chat.cabinet_id.is_(None), Chat.project_id.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self, user_id: int, chat_type: str, cabinet_id: int | None = None,
        service_request_id: int | None = None, project_id: int | None = None,
        bot_active: bool = True,
    ) -> Chat:
        chat = Chat(
            user_id=user_id, chat_type=chat_type, cabinet_id=cabinet_id,
            service_request_id=service_request_id, project_id=project_id,
            bot_active=bot_active,
        )
        self.session.add(chat)
        await self.session.flush()
        return chat

    async def find_by_service_request(self, service_request_id: int) -> Chat | None:
        result = await self.session.execute(
            select(Chat).where(Chat.service_request_id == service_request_id)
        )
        return result.scalar_one_or_none()

    # Чаты ШУ по всем пользователям сразу (не по одному) — нужно при удалении
    # ШУ, чтобы архивировать разом чаты всех, у кого он был, а не только
    # текущего вызывающего. Без фильтра по chat_type: cabinet_id ставится и
    # обычным чатам ШУ, и чатам заявок по этому ШУ — оба должны архивироваться
    # вместе с ним. Уже архивированные пропускаются.
    async def list_by_cabinet(self, cabinet_id: int) -> list[Chat]:
        result = await self.session.execute(
            select(Chat).where(
                Chat.cabinet_id == cabinet_id,
                Chat.archived_at.is_(None),
            )
        )
        return list(result.scalars().all())

    # Чаты проекта по всем пользователям сразу — тот же принцип, что у
    # list_by_cabinet, для архивации при удалении проекта.
    async def list_by_project(self, project_id: int) -> list[Chat]:
        result = await self.session.execute(
            select(Chat).where(
                Chat.project_id == project_id,
                Chat.archived_at.is_(None),
            )
        )
        return list(result.scalars().all())

    # Чаты ОДНОГО пользователя, относящиеся к проекту (его чат проекта плюс
    # чаты/заявки по шкафам этого проекта) — для архивации при выходе или
    # исключении пользователя из проекта: доступ теряет только он, чаты
    # остальных участников не трогаются.
    async def list_user_chats_for_project(
        self, user_id: int, project_id: int, cabinet_ids: list[int],
    ) -> list[Chat]:
        scope = [Chat.project_id == project_id]
        if cabinet_ids:
            scope.append(Chat.cabinet_id.in_(cabinet_ids))
        result = await self.session.execute(
            select(Chat).where(
                Chat.user_id == user_id,
                Chat.archived_at.is_(None),
                or_(*scope),
            )
        )
        return list(result.scalars().all())

    # Cabinet/Project сразу джойном (не по одному в цикле сервиса) — та же
    # причина, что у list_for_operator: раньше на каждый чат в списке уходило
    # по отдельному запросу за ШУ и проектом.
    async def list_for_user(
        self, user_id: int, chat_type: str | None = None, archived: bool = False,
    ) -> list[tuple]:
        from app.models.cabinets import Cabinet
        from app.models.project import Project
        conditions = [Chat.user_id == user_id]
        conditions.append(Chat.archived_at.isnot(None) if archived else Chat.archived_at.is_(None))
        if chat_type:
            conditions.append(Chat.chat_type == chat_type)
        result = await self.session.execute(
            select(Chat, Cabinet, Project)
            .outerjoin(Cabinet, Cabinet.id == Chat.cabinet_id)
            .outerjoin(Project, Project.id == Chat.project_id)
            .where(*conditions)
            .order_by(Chat.last_message_at.desc().nullslast(), Chat.created_at.desc())
        )
        return result.all()

    async def list_for_operator(
        self, search: str | None = None, chat_type: str | None = None, archived: bool = False,
    ) -> list[tuple]:
        from sqlalchemy import or_
        from app.models.cabinets import Cabinet
        from app.models.project import Project
        stmt = (
            select(Chat, User, Cabinet, Project)
            .outerjoin(User, User.id == Chat.user_id)
            .outerjoin(Cabinet, Cabinet.id == Chat.cabinet_id)
            .outerjoin(Project, Project.id == Chat.project_id)
            .where(Chat.chat_type.in_(VISIBLE_CHAT_TYPES))
        )
        stmt = stmt.where(Chat.archived_at.isnot(None) if archived else Chat.archived_at.is_(None))
        if chat_type:
            stmt = stmt.where(Chat.chat_type == chat_type)
        if search:
            pattern = f"%{escape_like(search)}%"
            stmt = stmt.where(or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
                Cabinet.object_number.ilike(pattern, escape="\\"),
                Cabinet.admin_internal_name.ilike(pattern, escape="\\"),
                Cabinet.type.ilike(pattern, escape="\\"),
                Project.name.ilike(pattern, escape="\\"),
            ))
        stmt = stmt.order_by(Chat.operator_requested.desc(), Chat.last_message_at.desc().nullslast())
        result = await self.session.execute(stmt)
        return result.all()

    async def get_unread_count(self, chat_id: int, user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Message.id)).where(
                Message.chat_id == chat_id,
                Message.sender_id != user_id,
                Message.is_read == False,
                Message.deleted_at.is_(None),
            )
        )
        return result.scalar() or 0

    async def get_unread_counts_batch(
        self, chat_ids: list[int], reader_id: int
    ) -> dict[int, int]:
        if not chat_ids:
            return {}
        result = await self.session.execute(
            select(Message.chat_id, func.count(Message.id))
            .where(
                Message.chat_id.in_(chat_ids),
                Message.sender_id != reader_id,
                Message.is_read == False,
                Message.deleted_at.is_(None),
            )
            .group_by(Message.chat_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def count_unread_chats(self, reader_id: int) -> int:
        result = await self.session.execute(
            select(func.count(func.distinct(Message.chat_id)))
            .join(Chat, Chat.id == Message.chat_id)
            .where(
                Message.sender_id != reader_id,
                Message.is_read == False,
                Message.deleted_at.is_(None),
                Chat.chat_type.in_(VISIBLE_CHAT_TYPES),
                Chat.archived_at.is_(None),
            )
        )
        return result.scalar() or 0


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, msg_id: int) -> Message | None:
        return await self.session.get(Message, msg_id)

    async def find_by_client_token(self, sender_id: int, client_token: str) -> Message | None:
        result = await self.session.execute(
            select(Message).where(
                Message.sender_id == sender_id,
                Message.client_token == client_token,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        chat_id: int,
        sender_id: int,
        text: str | None,
        reply_to_message_id: int | None = None,
        client_token: str | None = None,
    ) -> Message:
        msg = Message(
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            client_token=client_token,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def get_messages(
        self,
        chat_id: int,
        before_id: int | None = None,
        limit: int = 30,
        search: str | None = None,
    ) -> list[tuple]:
        stmt = (
            select(Message, User)
            .outerjoin(User, User.id == Message.sender_id)
            .where(Message.chat_id == chat_id)
        )
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        if search:
            stmt = stmt.where(Message.text.ilike(f"%{escape_like(search)}%", escape="\\"))
        stmt = stmt.order_by(Message.id.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.all()

    async def get_messages_around(
        self, chat_id: int, around_id: int, limit: int = 30
    ) -> list[tuple]:
        half = limit // 2
        base = (
            select(Message, User)
            .outerjoin(User, User.id == Message.sender_id)
            .where(Message.chat_id == chat_id)
        )
        before = (await self.session.execute(
            base.where(Message.id < around_id).order_by(Message.id.desc()).limit(half)
        )).all()
        after = (await self.session.execute(
            base.where(Message.id >= around_id).order_by(Message.id.asc()).limit(half + 1)
        )).all()
        # before пришёл desc — разворачиваем, затем склеиваем
        return list(reversed(before)) + list(after)

    async def get_messages_after(
        self, chat_id: int, after_id: int, limit: int = 30
    ) -> list[tuple]:
        stmt = (
            select(Message, User)
            .outerjoin(User, User.id == Message.sender_id)
            .where(Message.chat_id == chat_id, Message.id > after_id)
            .order_by(Message.id.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        # ASC даёт ближайшие к курсору первыми (старейшие из новых) — разворачиваем под общий порядок (новые → старые)
        return list(reversed(rows))

    async def get_attachments(self, message_ids: list[int]) -> dict[int, list[MessageAttachment]]:
        if not message_ids:
            return {}
        result = await self.session.execute(
            select(MessageAttachment).where(MessageAttachment.message_id.in_(message_ids))
        )
        mapping: dict[int, list] = {mid: [] for mid in message_ids}
        for att in result.scalars().all():
            mapping[att.message_id].append(att)
        return mapping

    async def get_reactions(self, message_ids: list[int]) -> dict[int, list[MessageReaction]]:
        if not message_ids:
            return {}
        result = await self.session.execute(
            select(MessageReaction).where(MessageReaction.message_id.in_(message_ids))
        )
        mapping: dict[int, list] = {mid: [] for mid in message_ids}
        for rxn in result.scalars().all():
            mapping[rxn.message_id].append(rxn)
        return mapping

    async def mark_read(self, chat_id: int, reader_id: int) -> list[int]:
        from sqlalchemy import update
        result = await self.session.execute(
            update(Message)
            .where(
                Message.chat_id == chat_id,
                Message.sender_id != reader_id,
                Message.is_read == False,
                Message.deleted_at.is_(None),
            )
            .values(is_read=True)
            .returning(Message.id)
        )
        return list(result.scalars().all())

    async def add_attachment(self, message_id: int, att_data: dict) -> MessageAttachment:
        att = MessageAttachment(message_id=message_id, **att_data)
        self.session.add(att)
        await self.session.flush()
        return att

    async def find_reaction(self, message_id: int, user_id: int, emoji: str) -> MessageReaction | None:
        result = await self.session.execute(
            select(MessageReaction).where(
                MessageReaction.message_id == message_id,
                MessageReaction.user_id == user_id,
                MessageReaction.emoji == emoji,
            )
        )
        return result.scalar_one_or_none()

    async def add_reaction(self, message_id: int, user_id: int, emoji: str) -> MessageReaction:
        rxn = MessageReaction(message_id=message_id, user_id=user_id, emoji=emoji)
        self.session.add(rxn)
        await self.session.flush()
        return rxn

    # Все файлы вложений чата, включая вложения удалённых (soft-delete)
    # сообщений — для очистки файлов на диске перед жёстким удалением чата
    # (см. ChatService.delete_chat/operator_delete_chat): CASCADE в БД уберёт
    # сами строки, а физические файлы сам по себе не тронет.
    async def list_all_attachment_urls(self, chat_id: int) -> list[str]:
        result = await self.session.execute(
            select(MessageAttachment.file_url)
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(Message.chat_id == chat_id, MessageAttachment.file_url.isnot(None))
        )
        return [url for (url,) in result.all()]

    async def get_chat_attachments(
        self, chat_id: int, attachment_type: str | None = None
    ) -> list[tuple]:
        from app.models.message_attchment import MessageAttachment
        stmt = (
            select(MessageAttachment, Message)
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(
                Message.chat_id == chat_id,
                Message.deleted_at.is_(None),
            )
        )
        if attachment_type:
            stmt = stmt.where(MessageAttachment.attachment_type == attachment_type)
        stmt = stmt.order_by(Message.created_at.desc())
        result = await self.session.execute(stmt)
        return result.all()

    async def get_last_messages_batch(self, chat_ids: list[int]) -> dict[int, Message]:
        if not chat_ids:
            return {}
        subq = (
            select(func.max(Message.id).label("max_id"))
            .where(Message.chat_id.in_(chat_ids), Message.deleted_at.is_(None))
            .group_by(Message.chat_id)
            .subquery()
        )
        result = await self.session.execute(
            select(Message).where(Message.id.in_(select(subq.c.max_id)))
        )
        return {msg.chat_id: msg for msg in result.scalars().all()}

    async def search_global(
        self, query: str, offset: int = 0, limit: int = 30
    ) -> tuple[list, int]:
        pattern = f"%{escape_like(query)}%"
        base_stmt = (
            select(Message, User, Chat)
            .outerjoin(User, User.id == Message.sender_id)
            .join(Chat, Chat.id == Message.chat_id)
            .where(
                Message.deleted_at.is_(None),
                Message.text.ilike(pattern, escape="\\"),
                Chat.chat_type.in_(VISIBLE_CHAT_TYPES),
            )
        )
        total = (await self.session.execute(
            select(func.count()).select_from(base_stmt.subquery())
        )).scalar() or 0
        result = await self.session.execute(
            base_stmt.order_by(Message.id.desc()).offset(offset).limit(limit)
        )
        return result.all(), total


class ChatSettingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user_id: int, chat_id: int | None) -> ChatUserSettings | None:
        stmt = select(ChatUserSettings).where(ChatUserSettings.user_id == user_id)
        if chat_id is None:
            stmt = stmt.where(ChatUserSettings.chat_id.is_(None))
        else:
            stmt = stmt.where(ChatUserSettings.chat_id == chat_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert(self, user_id: int, chat_id: int | None, data: dict) -> ChatUserSettings:
        obj = await self.get(user_id, chat_id)
        if obj is None:
            obj = ChatUserSettings(user_id=user_id, chat_id=chat_id, **data)
            self.session.add(obj)
        else:
            for k, v in data.items():
                setattr(obj, k, v)
        await self.session.flush()
        return obj

    async def delete_chat_override(self, user_id: int, chat_id: int) -> None:
        obj = await self.get(user_id, chat_id)
        if obj:
            await self.session.delete(obj)
            await self.session.flush()


class ChatPinRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def count(self, chat_id: int) -> int:
        result = await self.session.execute(
            select(func.count(ChatPinnedMessage.id)).where(ChatPinnedMessage.chat_id == chat_id)
        )
        return result.scalar() or 0

    async def exists(self, chat_id: int, message_id: int) -> bool:
        result = await self.session.execute(
            select(ChatPinnedMessage.id).where(
                ChatPinnedMessage.chat_id == chat_id,
                ChatPinnedMessage.message_id == message_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def add(self, chat_id: int, message_id: int, pinned_by: int) -> None:
        stmt = pg_insert(ChatPinnedMessage).values(
            chat_id=chat_id, message_id=message_id, pinned_by=pinned_by,
        ).on_conflict_do_nothing(index_elements=["chat_id", "message_id"])
        await self.session.execute(stmt)

    async def remove(self, chat_id: int, message_id: int) -> None:
        await self.session.execute(
            delete(ChatPinnedMessage).where(
                ChatPinnedMessage.chat_id == chat_id,
                ChatPinnedMessage.message_id == message_id,
            )
        )

    async def remove_all(self, chat_id: int) -> None:
        await self.session.execute(
            delete(ChatPinnedMessage).where(ChatPinnedMessage.chat_id == chat_id)
        )

    async def list_pins(self, chat_id: int) -> list[tuple]:
        result = await self.session.execute(
            select(Message, User)
            .join(ChatPinnedMessage, ChatPinnedMessage.message_id == Message.id)
            .outerjoin(User, User.id == Message.sender_id)
            .where(ChatPinnedMessage.chat_id == chat_id)
            .order_by(ChatPinnedMessage.pinned_at.desc())
        )
        return result.all()
