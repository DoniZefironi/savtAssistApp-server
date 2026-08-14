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


# Сигнал участникам проекта, что появился новый ШУ — без него мобильный клиент
# узнаёт об этом только при следующем самостоятельном рефетче GET /cabinets
# (см. README, раздел Realtime). Минимальный payload: сам список ШУ у каждого
# пользователя свой (персонализация, unread), поэтому клиент перезапрашивает
# его через REST, а не получает готовым в событии.
async def publish_cabinet_created(cabinet_id: int, project_id: int, user_ids: list[int]) -> None:
    envelope = {"type": "cabinet.created", "cabinet_id": cabinet_id, "project_id": project_id}
    for user_id in user_ids:
        await event_bus.publish(f"user_cabinets:{user_id}", envelope)


# Канал общий для мобильного WS (/user-events/cabinets/{id}/telemetry) и
# операторского SSE (/operator/events/cabinets/{id}/telemetry) — та же схема,
# что у "chat:{id}" выше. Минимальный payload: сам список решает, что показать
# (фильтр по названным регистрам и т.п.), поэтому клиент по сигналу просто
# перезапрашивает GET /cabinets/{id}/telemetry, а не получает событие целиком.
async def publish_telemetry_event(cabinet_id: int, event_id: int) -> None:
    await event_bus.publish(f"cabinet_telemetry:{cabinet_id}", {
        "type": "telemetry.created", "cabinet_id": cabinet_id, "event_id": event_id,
    })
