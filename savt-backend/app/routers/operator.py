from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.user import User
from app.schemas.chat import ChatListOut, MessageCreateIn, MessageOut
from app.services.chat_service import ChatService

router = APIRouter(prefix="/operator", tags=["operator"])

# Все чаты
@router.get("/chats", response_model=list[ChatListOut])
async def list_operator_chats(
    search: str | None = Query(None, min_length=1, max_length=200),
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ChatService(session).list_operator_chats(operator.id, search)

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

# Все чаты
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
    """Оператор берёт чат — бот замолкает."""
    await ChatService(session).operator_take_chat(chat_id)

# Вернуть боту
@router.post("/chats/{chat_id}/return-to-bot", status_code=status.HTTP_204_NO_CONTENT)
async def return_to_bot(
    chat_id: int,
    _: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    """Оператор возвращает чат боту."""
    await ChatService(session).operator_return_to_bot(chat_id)
