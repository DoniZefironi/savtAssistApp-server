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


async def publish_message_pinned(chat_id: int, message_id: int) -> None:
    await _publish_chat_event(chat_id, "message.pinned", {"id": message_id})


async def publish_message_unpinned(chat_id: int, message_id: int) -> None:
    await _publish_chat_event(chat_id, "message.unpinned", {"id": message_id})


async def publish_chat_updated(chat_id: int, chat_summary: dict[str, Any]) -> None:
    await event_bus.publish("operator_chats", {
        "type": "chat.updated", "chat_id": chat_id, "data": chat_summary,
    })


async def publish_chat_created(chat_id: int, chat_summary: dict[str, Any]) -> None:
    await event_bus.publish("operator_chats", {
        "type": "chat.created", "chat_id": chat_id, "data": chat_summary,
    })


async def _publish_chat_event(chat_id: int, event_type: str, data: dict[str, Any]) -> None:
    await event_bus.publish(f"chat:{chat_id}", {
        "type": event_type, "chat_id": chat_id, "data": data,
    })
