"""Публикация событий чата в event_bus для SSE.

Фасад над app.core.event_bus, чтобы chat_service.py, bot_service.py и
сервисы привязки ШУ не дублировали формат конверта события.
"""
from typing import Any

from app.core.event_bus import event_bus


async def publish_message_created(chat_id: int, message: dict[str, Any]) -> None:
    await _publish_chat_event(chat_id, "message.created", message)


async def publish_message_updated(chat_id: int, message: dict[str, Any]) -> None:
    await _publish_chat_event(chat_id, "message.updated", message)


async def publish_message_deleted(chat_id: int, message_id: int) -> None:
    await _publish_chat_event(chat_id, "message.deleted", {"id": message_id})


async def publish_reaction_changed(chat_id: int, message_id: int) -> None:
    await _publish_chat_event(chat_id, "message.reaction_changed", {"id": message_id})


async def publish_messages_read(chat_id: int, message_ids: list[int], reader_id: int) -> None:
    await _publish_chat_event(
        chat_id, "message.read", {"message_ids": message_ids, "reader_id": reader_id},
    )


async def publish_message_pinned(chat_id: int, message_id: int) -> None:
    await _publish_chat_event(chat_id, "message.pinned", {"id": message_id})


async def publish_message_unpinned(chat_id: int, message_id: int) -> None:
    await _publish_chat_event(chat_id, "message.unpinned", {"id": message_id})


async def publish_chat_updated(chat_id: int, chat_summary: dict[str, Any]) -> None:
    await _publish_chat_list_event("chat.updated", chat_id, chat_summary)


async def publish_chat_created(chat_id: int, chat_summary: dict[str, Any]) -> None:
    await _publish_chat_list_event("chat.created", chat_id, chat_summary)


async def _publish_chat_list_event(event_type: str, chat_id: int, chat_summary: dict[str, Any]) -> None:
    envelope = {"type": event_type, "chat_id": chat_id, "data": chat_summary}
    # Операторы видят все чаты сразу через общий канал...
    await event_bus.publish("operator_chats", envelope)
    # ...а владелец чата - через свой персональный канал (мобильное приложение,
    # см. user_events.py). chat_summary всегда несёт user_id (Chat.user_id обязателен).
    user_id = chat_summary.get("user_id")
    if user_id is not None:
        await event_bus.publish(f"user_chats:{user_id}", envelope)


async def _publish_chat_event(chat_id: int, event_type: str, data: dict[str, Any]) -> None:
    await event_bus.publish(f"chat:{chat_id}", {
        "type": event_type, "chat_id": chat_id, "data": data,
    })
