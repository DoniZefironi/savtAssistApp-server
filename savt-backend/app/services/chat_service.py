from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.models.chat import Chat
from app.models.message import Message
from app.repositories.chat import ChatPinRepository, ChatRepository, ChatSettingsRepository, MessageRepository
from app.services.realtime_events import (
    publish_chat_updated,
    publish_message_created,
    publish_message_deleted,
    publish_message_pinned,
    publish_message_unpinned,
    publish_message_updated,
    publish_messages_read,
    publish_reaction_changed,
)
from app.schemas.chat import (
    AttachmentOut,
    ChatAttachmentOut,
    ChatListOut,
    ChatOut,
    ChatSettingsIn,
    ChatSettingsOut,
    MessageCreateIn,
    MessageOut,
    MessageSearchOut,
    ReactionOut,
)

MAX_PINS_PER_CHAT = 10


class ChatService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.chat_repo = ChatRepository(session)
        self.msg_repo = MessageRepository(session)
        self.pin_repo = ChatPinRepository(session)

    # Возвращает вновь созданный чат поддержки (или None, если уже существовал) —
    # вызывающий код публикует chat.created после своего коммита
    async def ensure_support_and_notes(self, user_id: int) -> Chat | None:
        created_support_chat = None
        for chat_type in ("support", "notes"):
            existing = await self.chat_repo.find(user_id, chat_type)
            if existing is None:
                chat = await self.chat_repo.create(user_id, chat_type)
                if chat_type == "support":
                    created_support_chat = chat
        return created_support_chat

    async def ensure_cabinet_chat(self, user_id: int, cabinet_id: int) -> Chat:
        existing = await self.chat_repo.find(user_id, "cabinet", cabinet_id)
        if existing is None:
            existing = await self.chat_repo.create(user_id, "cabinet", cabinet_id)
        return existing

    # Чат заявки на обслуживание — один на заявку, привязан к тому же ШУ
    # (для отображения контекста в списке чатов), видим и пользователю, и операторам
    async def ensure_service_request_chat(
        self, user_id: int, service_request_id: int, cabinet_id: int
    ) -> Chat:
        existing = await self.chat_repo.find_by_service_request(service_request_id)
        if existing is None:
            # Бот в чатах заявок не участвует — там ведёт человек (см. send_message)
            existing = await self.chat_repo.create(
                user_id, "service_request", cabinet_id=cabinet_id,
                service_request_id=service_request_id, bot_active=False,
            )
        return existing

    # Архивация/разархивация чата заявки (закрытие/повторное открытие заявки) —
    # просто флаг, сообщения никуда не переносятся и не удаляются
    async def set_archived(self, chat_id: int, archived: bool) -> Chat | None:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            return None
        chat.archived_at = datetime.now(timezone.utc) if archived else None
        await self.session.commit()
        await publish_chat_updated(chat_id, chat_summary_dict(chat))
        return chat

    async def list_chats(
        self, user_id: int, chat_type: str | None = None, archived: bool = False,
    ) -> list[ChatListOut]:
        chats = await self.chat_repo.list_for_user(user_id, chat_type=chat_type, archived=archived)
        sr_ids = [c.service_request_id for c in chats if c.service_request_id is not None]
        sr_map = await self._get_service_requests(sr_ids)
        result = []
        for chat in chats:
            unread = await self.chat_repo.get_unread_count(chat.id, user_id)
            cabinet_name = None
            cabinet_object_number = None
            if chat.cabinet_id:
                from app.repositories.cabinet import CabinetRepository
                cab = await CabinetRepository(self.session).get_by_id(chat.cabinet_id)
                if cab:
                    cabinet_name = cab.admin_internal_name or cab.object_number
                    cabinet_object_number = cab.object_number

            last_text = None
            msgs = await self.msg_repo.get_messages(chat.id, limit=1)
            if msgs:
                msg, _ = msgs[0]
                last_text = msg.text if not msg.deleted_at else "Сообщение удалено"

            sr = sr_map.get(chat.service_request_id) if chat.service_request_id else None

            result.append(ChatListOut(
                id=chat.id,
                chat_type=chat.chat_type,
                cabinet_id=chat.cabinet_id,
                cabinet_name=cabinet_name,
                cabinet_object_number=cabinet_object_number,
                last_message_text=last_text,
                last_message_at=chat.last_message_at,
                unread_count=unread,
                problem_status=chat.problem_status,
                bot_active=chat.bot_active,
                operator_requested=chat.operator_requested,
                service_request_id=chat.service_request_id,
                service_request_type=sr.request_type if sr else None,
                service_request_status=sr.status if sr else None,
                service_request_description=sr.description if sr else None,
                service_request_created_at=sr.created_at if sr else None,
                archived_at=chat.archived_at,
            ))
        return result

    async def list_operator_chats(
        self, operator_id: int, search: str | None = None,
        chat_type: str | None = None, archived: bool = False,
    ) -> list[ChatListOut]:
        rows = await self.chat_repo.list_for_operator(search, chat_type=chat_type, archived=archived)
        if not rows:
            return []

        chat_ids = [chat.id for chat, _, _ in rows]
        unread_counts = await self.chat_repo.get_unread_counts_batch(chat_ids, operator_id)
        last_msgs = await self.msg_repo.get_last_messages_batch(chat_ids)
        sr_ids = [chat.service_request_id for chat, _, _ in rows if chat.service_request_id is not None]
        sr_map = await self._get_service_requests(sr_ids)

        result = []
        for chat, user, cabinet in rows:
            cabinet_name = None
            cabinet_object_number = None
            if cabinet:
                cabinet_name = cabinet.admin_internal_name or cabinet.object_number
                cabinet_object_number = cabinet.object_number

            last_msg = last_msgs.get(chat.id)
            last_text = last_msg.text if last_msg else None
            sr = sr_map.get(chat.service_request_id) if chat.service_request_id else None

            result.append(ChatListOut(
                id=chat.id,
                chat_type=chat.chat_type,
                cabinet_id=chat.cabinet_id,
                cabinet_name=cabinet_name,
                cabinet_object_number=cabinet_object_number,
                user_id=chat.user_id,
                user_name=user.full_name if user else None,
                last_message_text=last_text,
                last_message_at=chat.last_message_at,
                unread_count=unread_counts.get(chat.id, 0),
                problem_status=chat.problem_status,
                bot_active=chat.bot_active,
                operator_requested=chat.operator_requested,
                service_request_id=chat.service_request_id,
                service_request_type=sr.request_type if sr else None,
                service_request_status=sr.status if sr else None,
                service_request_description=sr.description if sr else None,
                service_request_created_at=sr.created_at if sr else None,
                archived_at=chat.archived_at,
            ))
        return result

    async def _get_service_requests(self, ids: list[int]) -> dict:
        if not ids:
            return {}
        from sqlalchemy import select
        from app.models.service_request import ServiceRequest
        result = await self.session.execute(select(ServiceRequest).where(ServiceRequest.id.in_(ids)))
        return {r.id: r for r in result.scalars().all()}

    async def get_cabinet_chat(self, user_id: int, cabinet_id: int) -> ChatOut:
        from app.repositories.cabinet import UserCabinetRepository
        if not await UserCabinetRepository(self.session).find(user_id, cabinet_id):
            raise PermissionDeniedError("У вас нет доступа к этому ШУ")
        chat = await self.chat_repo.find(user_id, "cabinet", cabinet_id)
        if chat is None:
            chat = await self.chat_repo.create(user_id, "cabinet", cabinet_id)
            await self.session.commit()
        return _to_chat_out(chat)

    async def send_message(
        self, chat_id: int, sender_id: int, data: MessageCreateIn
    ) -> MessageOut:
        chat = await self._get_chat_or_403(chat_id, sender_id)
        if chat.archived_at is not None:
            raise PermissionDeniedError("Чат архивирован — заявка закрыта, отправка сообщений недоступна")

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
        result = await self._build_message_out(msg, sender)

        await publish_message_created(chat_id, result.model_dump(mode="json"))
        await publish_chat_updated(chat_id, chat_summary_dict(chat, result.text))

        # Сообщение заявителя в чате заявки — дублируем в комментарий Bitrix-задачи
        # (ответы оператора/бота не синхронизируем)
        if (
            chat.chat_type == "service_request"
            and chat.service_request_id is not None
            and sender_id == chat.user_id
        ):
            from app.services.service_request_service import sync_message_to_bitrix
            sender_name = sender.full_name if sender else str(sender_id)
            attachment_urls = [att.file_url for att in data.attachments]
            sync_message_to_bitrix(chat.service_request_id, sender_name, data.text, attachment_urls)

        # Push пользователю от оператора
        if sender_id != chat.user_id:
            from app.services.push_service import send_push
            notif_body = (data.text or "Вложение")[:100]
            sender_name = sender.full_name if sender else "Оператор"
            await send_push(
                self.session, chat.user_id, sender_name, notif_body,
                {"chat_id": str(chat_id), "type": "chat_message"},
            )

        # Бот отвечает только на сообщения владельца чата — не в личных заметках
        # и не в чатах заявок (там ведёт человек, при необходимости эскалируется в Bitrix)
        if chat.user_id == sender_id and chat.bot_active and chat.chat_type not in ("notes", "service_request"):
            import asyncio
            import logging
            from app.database import AsyncSessionLocal
            from app.services.bot_service import handle_message

            _log = logging.getLogger(__name__)

            async def _bot_reply():
                try:
                    async with AsyncSessionLocal() as bot_session:
                        await handle_message(bot_session, chat.id, data.text)
                except Exception:
                    _log.exception("Bot reply failed for chat %s", chat.id)

            asyncio.create_task(_bot_reply())

        return result

    async def get_messages(
        self, chat_id: int, user_id: int, before_id: int | None, limit: int,
        search: str | None = None, around_id: int | None = None, after_id: int | None = None,
    ) -> list[MessageOut]:
        await self._get_chat_or_403(chat_id, user_id)
        if around_id is not None:
            rows = await self.msg_repo.get_messages_around(chat_id, around_id, limit)
        elif after_id is not None:
            rows = await self.msg_repo.get_messages_after(chat_id, after_id, limit)
        else:
            rows = await self.msg_repo.get_messages(chat_id, before_id, limit, search)
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
        message_ids = await self.msg_repo.mark_read(chat_id, user_id)
        await self.session.commit()
        # Раньше при "прочитано" ничего не публиковалось в SSE — собеседник узнавал
        # об этом только после ручного обновления страницы/рефетча
        if message_ids:
            await publish_messages_read(chat_id, message_ids, user_id)

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
        result = await self._build_message_out(msg, None)
        await publish_message_updated(chat_id, result.model_dump(mode="json"))
        return result

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
        await publish_message_deleted(chat_id, msg_id)

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
        await publish_reaction_changed(chat_id, msg_id)

    async def remove_reaction(
        self, chat_id: int, msg_id: int, user_id: int, emoji: str
    ) -> None:
        await self._get_chat_or_403(chat_id, user_id)
        rxn = await self.msg_repo.find_reaction(msg_id, user_id, emoji)
        if rxn is None:
            raise NotFoundError("Реакция не найдена")
        await self.session.delete(rxn)
        await self.session.commit()
        await publish_reaction_changed(chat_id, msg_id)

    async def delete_chat(self, chat_id: int, user_id: int) -> None:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        if chat.user_id != user_id:
            raise PermissionDeniedError("Нет доступа к этому чату")
        if chat.chat_type == "support":
            raise PermissionDeniedError("Чат поддержки нельзя удалить")
        await self.session.delete(chat)
        await self.session.commit()

    async def set_wallpaper(self, chat_id: int, user_id: int, wallpaper_url: str | None) -> ChatSettingsOut:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        if chat.user_id != user_id:
            raise PermissionDeniedError("Нет доступа к этому чату")
        repo = ChatSettingsRepository(self.session)
        obj = await repo.upsert(user_id, chat_id, {"wallpaper_url": wallpaper_url})
        await self.session.commit()
        return ChatSettingsOut.model_validate(obj)

    async def pin_message(self, chat_id: int, msg_id: int, user_id: int) -> list[MessageOut]:
        await self._get_chat_or_403(chat_id, user_id)
        msg = await self.msg_repo.get_by_id(msg_id)
        if msg is None or msg.chat_id != chat_id:
            raise NotFoundError("Сообщение не найдено")
        is_new_pin = False
        if not await self.pin_repo.exists(chat_id, msg_id):
            if await self.pin_repo.count(chat_id) >= MAX_PINS_PER_CHAT:
                raise HTTPException(
                    status_code=400,
                    detail=f"Достигнут лимит закреплённых сообщений ({MAX_PINS_PER_CHAT})",
                )
            await self.pin_repo.add(chat_id, msg_id, user_id)
            is_new_pin = True
        await self.session.commit()
        if is_new_pin:
            await publish_message_pinned(chat_id, msg_id)
        return await self.get_pinned_messages(chat_id)

    async def unpin_message(self, chat_id: int, msg_id: int, user_id: int) -> list[MessageOut]:
        await self._get_chat_or_403(chat_id, user_id)
        await self.pin_repo.remove(chat_id, msg_id)
        await self.session.commit()
        await publish_message_unpinned(chat_id, msg_id)
        return await self.get_pinned_messages(chat_id)

    async def unpin_all(self, chat_id: int, user_id: int) -> list[MessageOut]:
        await self._get_chat_or_403(chat_id, user_id)
        pinned_rows = await self.pin_repo.list_pins(chat_id)
        await self.pin_repo.remove_all(chat_id)
        await self.session.commit()
        for msg, _ in pinned_rows:
            await publish_message_unpinned(chat_id, msg.id)
        return []

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

    async def get_chat_attachments(
        self, chat_id: int, user_id: int, attachment_type: str | None = None
    ) -> list[ChatAttachmentOut]:
        await self._get_chat_or_403(chat_id, user_id)
        rows = await self.msg_repo.get_chat_attachments(chat_id, attachment_type)
        return [
            ChatAttachmentOut(
                id=att.id,
                message_id=att.message_id,
                attachment_type=att.attachment_type,
                file_url=att.file_url,
                file_name=att.file_name,
                file_size_bytes=att.file_size_bytes,
                mime_type=att.mime_type,
                duration_seconds=att.duration_seconds,
                created_at=msg.created_at,
            )
            for att, msg in rows
        ]

    async def operator_delete_chat(self, chat_id: int) -> None:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        await self.session.delete(chat)
        await self.session.commit()

    async def clear_chat_messages(self, chat_id: int) -> None:
        from sqlalchemy import update as sa_update
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        now = datetime.now(timezone.utc)
        await self.session.execute(
            sa_update(Message)
            .where(Message.chat_id == chat_id, Message.deleted_at.is_(None))
            .values(deleted_at=now, text=None)
        )
        await self.session.commit()

    async def get_chat_settings(self, user_id: int, chat_id: int | None) -> ChatSettingsOut:
        if chat_id is not None:
            chat = await self.chat_repo.get_by_id(chat_id)
            if chat is None:
                raise NotFoundError("Чат не найден")
        repo = ChatSettingsRepository(self.session)
        obj = await repo.get(user_id, chat_id)
        if obj is None and chat_id is not None:
            obj = await repo.get(user_id, None)
        if obj is None:
            from app.models.chat_user_settings import ChatUserSettings
            obj = ChatUserSettings(user_id=user_id, chat_id=chat_id)
        return ChatSettingsOut.model_validate(obj)

    async def update_chat_settings(
        self, user_id: int, chat_id: int | None, data: ChatSettingsIn
    ) -> ChatSettingsOut:
        if chat_id is not None:
            chat = await self.chat_repo.get_by_id(chat_id)
            if chat is None:
                raise NotFoundError("Чат не найден")
        repo = ChatSettingsRepository(self.session)
        obj = await repo.upsert(user_id, chat_id, data.model_dump(exclude_unset=True))
        await self.session.commit()
        return ChatSettingsOut.model_validate(obj)

    async def reset_chat_settings(self, user_id: int, chat_id: int) -> None:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        repo = ChatSettingsRepository(self.session)
        await repo.delete_chat_override(user_id, chat_id)
        await self.session.commit()

    async def search_messages_global(
        self, query: str, page: int, size: int
    ) -> object:
        from app.schemas.pagination import make_page
        from app.repositories.cabinet import CabinetRepository
        offset = (page - 1) * size
        rows, total = await self.msg_repo.search_global(query, offset, size)
        cab_cache: dict[int, str | None] = {}
        items: list[MessageSearchOut] = []
        for msg, sender, chat in rows:
            cab_obj_num: str | None = None
            if chat.cabinet_id:
                if chat.cabinet_id not in cab_cache:
                    cab = await CabinetRepository(self.session).get_by_id(chat.cabinet_id)
                    cab_cache[chat.cabinet_id] = cab.object_number if cab else None
                cab_obj_num = cab_cache[chat.cabinet_id]
            atts = (await self.msg_repo.get_attachments([msg.id])).get(msg.id, [])
            items.append(MessageSearchOut(
                id=msg.id,
                chat_id=chat.id,
                chat_type=chat.chat_type,
                cabinet_object_number=cab_obj_num,
                chat_user_id=chat.user_id,
                sender_id=msg.sender_id,
                sender_name=sender.full_name if sender else None,
                text=msg.text,
                created_at=msg.created_at,
                attachments=[AttachmentOut.model_validate(a) for a in atts],
            ))
        return make_page(items, total, page, size)

    # Публичная обёртка над _get_chat_or_403 — для мест, которым нужен просто
    # bool, а не исключение (например, авторизация WS-подключения к чату)
    async def has_access(self, chat_id: int, user_id: int) -> bool:
        try:
            await self._get_chat_or_403(chat_id, user_id)
            return True
        except (NotFoundError, PermissionDeniedError):
            return False

    async def _get_chat_or_403(self, chat_id: int, user_id: int) -> Chat:
        chat = await self.chat_repo.get_by_id(chat_id)
        if chat is None:
            raise NotFoundError("Чат не найден")
        # Владелец чата или оператор/админ (проверяется на уровне роутера),
        # но личные заметки ("notes") доступны только владельцу
        if chat.user_id != user_id:
            if chat.chat_type == "notes":
                raise PermissionDeniedError("Нет доступа к этому чату")
            from app.repositories.user import UserRepository
            from app.models.role import Role
            user = await UserRepository(self.session).get_by_id(user_id)
            if user:
                role = await self.session.get(Role, user.role_id)
                if role and role.name in ("operator", "admin", "superadmin"):
                    return chat
            raise PermissionDeniedError("Нет доступа к этому чату")
        return chat

    async def get_pinned_messages(self, chat_id: int) -> list[MessageOut]:
        rows = await self.pin_repo.list_pins(chat_id)
        if not rows:
            return []
        msg_ids = [m.id for m, _ in rows]
        atts = await self.msg_repo.get_attachments(msg_ids)
        rxns = await self.msg_repo.get_reactions(msg_ids)
        return [
            _build_message(msg, user, atts.get(msg.id, []), rxns.get(msg.id, []))
            for msg, user in rows
        ]

    async def _build_message_out(self, msg, user) -> MessageOut:
        atts = (await self.msg_repo.get_attachments([msg.id])).get(msg.id, [])
        rxns = (await self.msg_repo.get_reactions([msg.id])).get(msg.id, [])
        return _build_message(msg, user, atts, rxns)


def chat_summary_dict(chat: Chat, last_text: str | None = None, user_name: str | None = None) -> dict:
    """Снимок чата для SSE-события (chat.updated/chat.created).

    Не претендует на полноту (например, unread_count зависит от конкретного
    оператора-получателя и здесь не считается) — фронт использует событие
    как сигнал инвалидировать кэш и перезапросить актуальный список."""
    return {
        "id": chat.id,
        "chat_type": chat.chat_type,
        "cabinet_id": chat.cabinet_id,
        "service_request_id": chat.service_request_id,
        "user_id": chat.user_id,
        "user_name": user_name,
        "last_message_text": last_text,
        "last_message_at": chat.last_message_at.isoformat() if chat.last_message_at else None,
        "problem_status": chat.problem_status,
        "bot_active": chat.bot_active,
        "operator_requested": chat.operator_requested,
        "archived_at": chat.archived_at.isoformat() if chat.archived_at else None,
    }


def _to_chat_out(chat: Chat) -> ChatOut:
    return ChatOut(
        id=chat.id,
        chat_type=chat.chat_type,
        cabinet_id=chat.cabinet_id,
        service_request_id=chat.service_request_id,
        problem_status=chat.problem_status,
        bot_active=chat.bot_active,
        operator_requested=chat.operator_requested,
        archived_at=chat.archived_at,
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
