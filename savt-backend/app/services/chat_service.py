from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.models.chat import Chat
from app.repositories.chat import ChatRepository, MessageRepository
from app.schemas.chat import (
    AttachmentOut,
    ChatListOut,
    ChatOut,
    MessageCreateIn,
    MessageOut,
    ReactionOut,
)


class ChatService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.chat_repo = ChatRepository(session)
        self.msg_repo = MessageRepository(session)

    async def ensure_support_and_notes(self, user_id: int) -> None:
        """Создаёт support и notes чаты если их нет. Вызывается при регистрации."""
        for chat_type in ("support", "notes"):
            existing = await self.chat_repo.find(user_id, chat_type)
            if existing is None:
                await self.chat_repo.create(user_id, chat_type)

    async def ensure_cabinet_chat(self, user_id: int, cabinet_id: int) -> Chat:
        """Создаёт cabinet-чат если его нет. Вызывается при привязке ШУ."""
        existing = await self.chat_repo.find(user_id, "cabinet", cabinet_id)
        if existing is None:
            existing = await self.chat_repo.create(user_id, "cabinet", cabinet_id)
        return existing

    async def list_chats(self, user_id: int) -> list[ChatListOut]:
        chats = await self.chat_repo.list_for_user(user_id)
        result = []
        for chat in chats:
            unread = await self.chat_repo.get_unread_count(chat.id, user_id)
            cabinet_name = None
            if chat.cabinet_id:
                from app.repositories.cabinet import CabinetRepository
                cab = await CabinetRepository(self.session).get_by_id(chat.cabinet_id)
                if cab:
                    cabinet_name = cab.admin_internal_name or cab.object_number

            last_text = None
            msgs = await self.msg_repo.get_messages(chat.id, limit=1)
            if msgs:
                msg, _ = msgs[0]
                last_text = msg.text if not msg.deleted_at else "Сообщение удалено"

            result.append(ChatListOut(
                id=chat.id,
                chat_type=chat.chat_type,
                cabinet_id=chat.cabinet_id,
                cabinet_name=cabinet_name,
                last_message_text=last_text,
                last_message_at=chat.last_message_at,
                unread_count=unread,
                problem_status=chat.problem_status,
                bot_active=chat.bot_active,
                operator_requested=chat.operator_requested,
            ))
        return result

    async def list_operator_chats(self, operator_id: int) -> list[ChatListOut]:
        chats = await self.chat_repo.list_for_operator()
        result = []
        for chat in chats:
            unread = await self.chat_repo.get_unread_count(chat.id, operator_id)

            cabinet_name = None
            if chat.cabinet_id:
                from app.repositories.cabinet import CabinetRepository
                cab = await CabinetRepository(self.session).get_by_id(chat.cabinet_id)
                if cab:
                    cabinet_name = cab.admin_internal_name or cab.object_number

            from app.repositories.user import UserRepository
            user = await UserRepository(self.session).get_by_id(chat.user_id)

            last_text = None
            msgs = await self.msg_repo.get_messages(chat.id, limit=1)
            if msgs:
                msg, _ = msgs[0]
                last_text = msg.text if not msg.deleted_at else "Сообщение удалено"

            result.append(ChatListOut(
                id=chat.id,
                chat_type=chat.chat_type,
                cabinet_id=chat.cabinet_id,
                cabinet_name=cabinet_name,
                user_id=chat.user_id,
                user_name=user.full_name if user else None,
                last_message_text=last_text,
                last_message_at=chat.last_message_at,
                unread_count=unread,
                problem_status=chat.problem_status,
                bot_active=chat.bot_active,
                operator_requested=chat.operator_requested,
            ))
        return result

    async def get_cabinet_chat(self, user_id: int, cabinet_id: int) -> ChatOut:
        chat = await self.chat_repo.find(user_id, "cabinet", cabinet_id)
        if chat is None:
            chat = await self.chat_repo.create(user_id, "cabinet", cabinet_id)
            await self.session.commit()
        return _to_chat_out(chat)

    async def send_message(
        self, chat_id: int, sender_id: int, data: MessageCreateIn
    ) -> MessageOut:
        chat = await self._get_chat_or_403(chat_id, sender_id)

        reply_to = data.reply_to_message_id if data.reply_to_message_id else None
        msg = await self.msg_repo.create(
            chat_id=chat_id,
            sender_id=sender_id,
            text=data.text,
            reply_to_message_id=reply_to,
        )

        for att in data.attachments:
            await self.msg_repo.add_attachment(msg.id, {
                "attachment_type": _attachment_type(att.mime_type),
                "file_url": att.file_url,
                "file_name": att.file_name,
                "file_size_bytes": att.file_size_bytes,
                "mime_type": att.mime_type,
                "duration_seconds": att.duration_seconds,
            })
        chat.last_message_at = datetime.now(timezone.utc)
        if chat.user_id == sender_id:
            chat.last_user_message_at = chat.last_message_at

        await self.session.commit()
        await self.session.refresh(msg)
        from app.repositories.user import UserRepository
        sender = await UserRepository(self.session).get_by_id(sender_id)
        return await self._build_message_out(msg, sender)

    async def get_messages(
        self, chat_id: int, user_id: int, before_id: int | None, limit: int
    ) -> list[MessageOut]:
        await self._get_chat_or_403(chat_id, user_id)
        rows = await self.msg_repo.get_messages(chat_id, before_id, limit)
        if not rows:
            return []
        msg_ids = [m.id for m, _ in rows]
        atts = await self.msg_repo.get_attachments(msg_ids)
        rxns = await self.msg_repo.get_reactions(msg_ids)
        return [
            _build_message(msg, user, atts.get(msg.id, []), rxns.get(msg.id, []))
            for msg, user in rows
        ]

    async def mark_read(self, chat_id: int, user_id: int) -> None:
        await self._get_chat_or_403(chat_id, user_id)
        await self.msg_repo.mark_read(chat_id, user_id)
        await self.session.commit()

    async def edit_message(
        self, chat_id: int, msg_id: int, user_id: int, text: str
    ) -> MessageOut:
        await self._get_chat_or_403(chat_id, user_id)
        msg = await self.msg_repo.get_by_id(msg_id)
        if msg is None or msg.chat_id != chat_id:
            raise NotFoundError("Сообщение не найдено")
        if msg.sender_id != user_id:
            raise PermissionDeniedError("Нельзя редактировать чужое сообщение")
        if msg.deleted_at:
            raise PermissionDeniedError("Нельзя редактировать удалённое сообщение")
        msg.text = text
        msg.edited_at = datetime.now(timezone.utc)
        await self.session.commit()
        return await self._build_message_out(msg, None)

    async def delete_message(self, chat_id: int, msg_id: int, user_id: int) -> None:
        await self._get_chat_or_403(chat_id, user_id)
        msg = await self.msg_repo.get_by_id(msg_id)
        if msg is None or msg.chat_id != chat_id:
            raise NotFoundError("Сообщение не найдено")
        if msg.sender_id != user_id:
            raise PermissionDeniedError("Нельзя удалить чужое сообщение")
        msg.deleted_at = datetime.now(timezone.utc)
        msg.text = None
        await self.session.commit()

    async def add_reaction(
        self, chat_id: int, msg_id: int, user_id: int, emoji: str
    ) -> None:
        await self._get_chat_or_403(chat_id, user_id)
        msg = await self.msg_repo.get_by_id(msg_id)
        if msg is None or msg.chat_id != chat_id:
            raise NotFoundError("Сообщение не найдено")
        existing = await self.msg_repo.find_reaction(msg_id, user_id, emoji)
        if existing is not None:
            raise AlreadyExistsError("Реакция уже поставлена")
        await self.msg_repo.add_reaction(msg_id, user_id, emoji)
        await self.session.commit()

    async def remove_reaction(
        self, chat_id: int, msg_id: int, user_id: int, emoji: str
    ) -> None:
        await self._get_chat_or_403(chat_id, user_id)
        rxn = await self.msg_repo.find_reaction(msg_id, user_id, emoji)
        if rxn is None:
            raise NotFoundError("Реакция не найдена")
        await self.session.delete(rxn)
        await self.session.commit()

    async def operator_take_chat(self, chat_id: int) -> None:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        chat.bot_active = False
        chat.operator_requested = False
        await self.session.commit()

    async def operator_return_to_bot(self, chat_id: int) -> None:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        chat.bot_active = True
        chat.bot_no_count = 0
        await self.session.commit()

    async def _get_chat_or_403(self, chat_id: int, user_id: int) -> Chat:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        # Владелец чата или оператор/админ (проверяется на уровне роутера)
        if chat.user_id != user_id:
            from app.repositories.user import UserRepository
            from app.models.role import Role
            user = await UserRepository(self.session).get_by_id(user_id)
            if user:
                role = await self.session.get(Role, user.role_id)
                if role and role.name in ("operator", "admin"):
                    return chat
            raise PermissionDeniedError("Нет доступа к этому чату")
        return chat

    async def _build_message_out(self, msg, user) -> MessageOut:
        atts = (await self.msg_repo.get_attachments([msg.id])).get(msg.id, [])
        rxns = (await self.msg_repo.get_reactions([msg.id])).get(msg.id, [])
        sender_name = user.full_name if user else None
        return _build_message(msg, user, atts, rxns)


def _to_chat_out(chat: Chat) -> ChatOut:
    return ChatOut(
        id=chat.id,
        chat_type=chat.chat_type,
        cabinet_id=chat.cabinet_id,
        problem_status=chat.problem_status,
        bot_active=chat.bot_active,
        operator_requested=chat.operator_requested,
        created_at=chat.created_at,
    )


def _build_message(msg, user, atts, rxns) -> MessageOut:
    return MessageOut(
        id=msg.id,
        chat_id=msg.chat_id,
        sender_id=msg.sender_id,
        sender_name=user.full_name if user else None,
        text=msg.text,
        reply_to_message_id=msg.reply_to_message_id,
        is_read=msg.is_read,
        created_at=msg.created_at,
        edited_at=msg.edited_at,
        deleted_at=msg.deleted_at,
        attachments=[AttachmentOut.model_validate(a) for a in atts],
        reactions=[ReactionOut.model_validate(r) for r in rxns],
    )


def _attachment_type(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "voice"
    return "document"
