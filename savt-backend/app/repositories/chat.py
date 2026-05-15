from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat
from app.models.message import Message
from app.models.message_attchment import MessageAttachment
from app.models.message_reaction import MessageReaction
from app.models.user import User


class ChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, chat_id: int) -> Chat | None:
        return await self.session.get(Chat, chat_id)

    async def find(self, user_id: int, chat_type: str, cabinet_id: int | None = None) -> Chat | None:
        stmt = select(Chat).where(
            Chat.user_id == user_id,
            Chat.chat_type == chat_type,
        )
        if cabinet_id is not None:
            stmt = stmt.where(Chat.cabinet_id == cabinet_id)
        else:
            stmt = stmt.where(Chat.cabinet_id.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, user_id: int, chat_type: str, cabinet_id: int | None = None) -> Chat:
        chat = Chat(user_id=user_id, chat_type=chat_type, cabinet_id=cabinet_id)
        self.session.add(chat)
        await self.session.flush()
        return chat

    async def list_for_user(self, user_id: int) -> list[Chat]:
        result = await self.session.execute(
            select(Chat)
            .where(Chat.user_id == user_id)
            .order_by(Chat.last_message_at.desc().nullslast(), Chat.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_for_operator(self) -> list[Chat]:
        """Все cabinet-чаты + открытые support-чаты."""
        from sqlalchemy import or_
        result = await self.session.execute(
            select(Chat)
            .where(
                or_(
                    Chat.chat_type == "cabinet",
                    Chat.chat_type == "support",
                )
            )
            .order_by(Chat.operator_requested.desc(), Chat.last_message_at.desc().nullslast())
        )
        return list(result.scalars().all())

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


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, msg_id: int) -> Message | None:
        return await self.session.get(Message, msg_id)

    async def create(
        self,
        chat_id: int,
        sender_id: int,
        text: str | None,
        reply_to_message_id: int | None = None,
    ) -> Message:
        msg = Message(
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def get_messages(
        self,
        chat_id: int,
        before_id: int | None = None,
        limit: int = 30,
    ) -> list[tuple]:
        """Возвращает (Message, User) — от новых к старым."""
        stmt = (
            select(Message, User)
            .join(User, User.id == Message.sender_id)
            .where(Message.chat_id == chat_id)
        )
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        stmt = stmt.order_by(Message.id.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.all()

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

    async def mark_read(self, chat_id: int, reader_id: int) -> None:
        """Отмечает все чужие непрочитанные сообщения как прочитанные."""
        from sqlalchemy import update
        await self.session.execute(
            update(Message)
            .where(
                Message.chat_id == chat_id,
                Message.sender_id != reader_id,
                Message.is_read == False,
                Message.deleted_at.is_(None),
            )
            .values(is_read=True)
        )

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
