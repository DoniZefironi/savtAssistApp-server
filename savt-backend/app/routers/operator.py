from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.user import User
from app.schemas.chat import ChatAttachmentOut, ChatListOut, ChatOut, MessageCreateIn, MessageOut, MessageSearchOut
from app.schemas.pagination import PageOut
from app.services.chat_service import ChatService

router = APIRouter(prefix="/operator", tags=["operator"])


# Поиск сообщений по всем чатам — ДОЛЖЕН быть ДО /chats/{chat_id}
@router.get("/messages", response_model=PageOut[MessageSearchOut])
async def search_messages_global(
    q: str = Query(..., min_length=1, max_length=200),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).search_messages_global(q, page, size)


# Количество чатов с непрочитанными — ДОЛЖЕН быть ДО /chats/{chat_id}
@router.get("/chats/unread-count")
async def get_unread_chats_count(
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    from app.repositories.chat import ChatRepository
    count = await ChatRepository(session).count_unread_chats(operator.id)
    return {"count": count}


# Все чаты
@router.get("/chats", response_model=list[ChatListOut])
async def list_operator_chats(
    search: str | None = Query(None, min_length=1, max_length=200),
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).list_operator_chats(operator.id, search)


# Закреплённое сообщение (null если не закреплено)
@router.get("/chats/{chat_id}/pinned", response_model=MessageOut | None)
async def get_pinned_message(
    chat_id: int,
    _: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).get_pinned_message(chat_id)


# Получить сообщения
@router.get("/chats/{chat_id}/messages", response_model=list[MessageOut])
async def get_messages(
    chat_id: int,
    before_id: int | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
    search: str | None = Query(None, min_length=1, max_length=200),
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).get_messages(chat_id, operator.id, before_id, limit, search)


# Отправить сообщение
@router.post("/chats/{chat_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def send_message(
    chat_id: int,
    payload: MessageCreateIn,
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).send_message(chat_id, operator.id, payload)


# Взять чат
@router.post("/chats/{chat_id}/take", status_code=status.HTTP_204_NO_CONTENT)
async def take_chat(
    chat_id: int,
    _: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).operator_take_chat(chat_id)


# Вернуть боту
@router.post("/chats/{chat_id}/return-to-bot", status_code=status.HTTP_204_NO_CONTENT)
async def return_to_bot(
    chat_id: int,
    _: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).operator_return_to_bot(chat_id)


# Удалить чат
@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: int,
    _: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).operator_delete_chat(chat_id)


# Очистить историю сообщений (soft-delete всех сообщений)
@router.delete("/chats/{chat_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_chat_messages(
    chat_id: int,
    _: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    await ChatService(session).clear_chat_messages(chat_id)


# Закрепить сообщение
@router.put("/chats/{chat_id}/pin/{msg_id}", response_model=ChatOut)
async def pin_message(
    chat_id: int,
    msg_id: int,
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).pin_message(chat_id, msg_id, operator.id)


# Открепить сообщение
@router.delete("/chats/{chat_id}/pin", response_model=ChatOut)
async def unpin_message(
    chat_id: int,
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).unpin_message(chat_id, operator.id)


# Все вложения чата
@router.get("/chats/{chat_id}/attachments", response_model=list[ChatAttachmentOut])
async def get_chat_attachments(
    chat_id: int,
    type: str | None = Query(None, pattern="^(image|voice|document|video)$"),
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).get_chat_attachments(chat_id, operator.id, type)
