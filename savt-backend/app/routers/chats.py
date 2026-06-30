from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.chat import ChatAttachmentOut, ChatListOut, ChatOut, ChatSettingsIn, ChatSettingsOut, MessageCreateIn, MessageOut, WallpaperIn
from app.services.chat_service import ChatService

router = APIRouter(tags=["chats"])


# Глобальные настройки вида чата (цвета, шрифт) — ДОЛЖНЫ быть ДО /chats/{chat_id}
@router.get("/chats/settings", response_model=ChatSettingsOut)
async def get_global_chat_settings(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).get_chat_settings(current_user.id, None)


@router.patch("/chats/settings", response_model=ChatSettingsOut)
async def update_global_chat_settings(
    payload: ChatSettingsIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).update_chat_settings(current_user.id, None, payload)


# Список чатов пользователя
@router.get("/chats", response_model=list[ChatListOut])
async def list_chats(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).list_chats(current_user.id)

# Получить/создать чат ШУ
@router.get("/cabinets/{cabinet_id}/chat", response_model=ChatOut)
async def get_cabinet_chat(
    cabinet_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).get_cabinet_chat(current_user.id, cabinet_id)

# История
@router.get("/chats/{chat_id}/messages", response_model=list[MessageOut])
async def get_messages(
    chat_id: int,
    before_id: int | None = Query(None, gt=0),
    around_id: int | None = Query(None, gt=0),
    after_id: int | None = Query(None, gt=0),
    limit: int = Query(30, ge=1, le=100),
    search: str | None = Query(None, min_length=1, max_length=200),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).get_messages(
        chat_id, current_user.id, before_id, limit, search, around_id, after_id
    )

# Отправить
@router.post("/chats/{chat_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def send_message(
    chat_id: int,
    payload: MessageCreateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).send_message(chat_id, current_user.id, payload)

# Прочитать всё
@router.post("/chats/{chat_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).mark_read(chat_id, current_user.id)

# Редактировать сообщение
@router.patch("/chats/{chat_id}/messages/{msg_id}", response_model=MessageOut)
async def edit_message(
    chat_id: int,
    msg_id: int,
    payload: MessageCreateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not payload.text:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Текст обязателен при редактировании")
    return await ChatService(session).edit_message(chat_id, msg_id, current_user.id, payload.text)

# Удалить сообщение
@router.delete("/chats/{chat_id}/messages/{msg_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    chat_id: int,
    msg_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).delete_message(chat_id, msg_id, current_user.id)

# Поставить реакцию
@router.post("/chats/{chat_id}/messages/{msg_id}/reactions/{emoji}", status_code=status.HTTP_204_NO_CONTENT)
async def add_reaction(
    chat_id: int,
    msg_id: int,
    emoji: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).add_reaction(chat_id, msg_id, current_user.id, emoji)

# Удалить реакцию
@router.delete("/chats/{chat_id}/messages/{msg_id}/reactions/{emoji}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_reaction(
    chat_id: int,
    msg_id: int,
    emoji: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).remove_reaction(chat_id, msg_id, current_user.id, emoji)


# Удалить чат
@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).delete_chat(chat_id, current_user.id)


# Обои чата (личные — сохраняются в настройках пользователя)
@router.patch("/chats/{chat_id}/wallpaper", response_model=ChatSettingsOut)
async def set_wallpaper(
    chat_id: int,
    payload: WallpaperIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).set_wallpaper(chat_id, current_user.id, payload.wallpaper_url)


# Закрепить сообщение
@router.put("/chats/{chat_id}/pin/{msg_id}", response_model=ChatOut)
async def pin_message(
    chat_id: int,
    msg_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).pin_message(chat_id, msg_id, current_user.id)


# Открепить сообщение
@router.delete("/chats/{chat_id}/pin", response_model=ChatOut)
async def unpin_message(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).unpin_message(chat_id, current_user.id)


# Настройки вида конкретного чата (цвета, шрифт) — per-chat override
@router.get("/chats/{chat_id}/settings", response_model=ChatSettingsOut)
async def get_chat_settings(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).get_chat_settings(current_user.id, chat_id)


@router.patch("/chats/{chat_id}/settings", response_model=ChatSettingsOut)
async def update_chat_settings(
    chat_id: int,
    payload: ChatSettingsIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).update_chat_settings(current_user.id, chat_id, payload)


@router.delete("/chats/{chat_id}/settings", status_code=status.HTTP_204_NO_CONTENT)
async def reset_chat_settings(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).reset_chat_settings(current_user.id, chat_id)


# Все вложения чата (изображения, голосовые, файлы, видео)
@router.get("/chats/{chat_id}/attachments", response_model=list[ChatAttachmentOut])
async def get_chat_attachments(
    chat_id: int,
    type: str | None = Query(None, pattern="^(image|voice|document|video)$"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).get_chat_attachments(chat_id, current_user.id, type)
