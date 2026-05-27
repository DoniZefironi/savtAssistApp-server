from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chat import Chat
from app.models.embedding import Embedding
from app.models.message import Message
from app.services import yandex_service

_BOT_USER_LOGIN = "__ася__"
_BOT_NAME = "Ася"

_SYSTEM_PROMPT = """Ты — помощник Ася, виртуальный ассистент сервисной службы SAVT.
Ты помогаешь пользователям с вопросами по шкафам управления (ШУ): гарантия, документация, неисправности, обслуживание.

Правила:
- Отвечай чётко, кратко, структурированно.
- Используй только информацию из предоставленного контекста.
- Если контекст не содержит ответа — честно скажи об этом и задай уточняющий вопрос.
- Не придумывай информацию.
- Отвечай на русском языке.
- Обращайся к пользователю на "вы".
- Если в контексте есть фраза, подтверждающая твой ответ, — добавь её в конце в формате:
  > "цитата (1–2 предложения)"
  Если подходящей цитаты нет — не добавляй блок цитаты."""

_NEGATIVE_KEYWORDS = {
    "нет", "не помогло", "не работает", "не решило", "не то",
    "всё равно", "по-прежнему", "проблема осталась", "не помог",
}


async def get_bot_user_id(session: AsyncSession) -> int | None:
    from app.models.user import User
    result = await session.execute(
        select(User).where(User.login == _BOT_USER_LOGIN)
    )
    user = result.scalar_one_or_none()
    return user.id if user else None


async def ensure_bot_user(session: AsyncSession) -> int:
    """Создаёт системного пользователя-бота если его нет. Возвращает его id."""
    from app.models.user import User
    from app.core.security import hash_password
    import secrets

    result = await session.execute(
        select(User).where(User.login == _BOT_USER_LOGIN)
    )
    bot = result.scalar_one_or_none()
    if bot:
        return bot.id

    bot = User(
        login=_BOT_USER_LOGIN,
        full_name=_BOT_NAME,
        hashed_password=hash_password(secrets.token_hex(32)),
        role_id=1,
        is_phone_verified=True,
        is_active=True,
        is_verified=True,
    )
    session.add(bot)
    await session.flush()
    await session.commit()
    return bot.id


_SOURCE_LABELS = {
    "faq": "FAQ",
    "kb_article": "База знаний",
    "document": "Документация ШУ",
}


async def _retrieve_context(
    session: AsyncSession, query: str, cabinet_id: int | None, k: int = 3
) -> list[dict]:
    """RAG: возвращает k чанков с меткой источника."""
    vec = cast(await yandex_service.embed_query(query), Vector(256))

    stmt = (
        select(Embedding.content, Embedding.source_type, Embedding.meta)
        .order_by(Embedding.embedding.op("<=>")(vec))
        .limit(k)
    )

    def _row_to_dict(row) -> dict:
        content, source_type, meta = row
        label = _SOURCE_LABELS.get(source_type, source_type)
        title = (meta or {}).get("title", "")
        source = f"{label}: {title}" if title else label
        return {"content": content, "source": source}

    if cabinet_id:
        cabinet_stmt = (
            select(Embedding.content, Embedding.source_type, Embedding.meta)
            .where(
                Embedding.source_type == "document",
                Embedding.meta["cabinet_id"].astext == str(cabinet_id),
            )
            .order_by(Embedding.embedding.op("<=>")(vec))
            .limit(2)
        )
        cabinet_rows = (await session.execute(cabinet_stmt)).all()
        remaining = k - len(cabinet_rows)
        general_rows = (await session.execute(stmt.limit(remaining))).all() if remaining > 0 else []
        return [_row_to_dict(r) for r in cabinet_rows + general_rows]

    return [_row_to_dict(r) for r in (await session.execute(stmt)).all()]


def _is_negative(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in _NEGATIVE_KEYWORDS)


async def _send_bot_message(session: AsyncSession, chat: Chat, bot_user_id: int, text: str) -> None:
    msg = Message(
        chat_id=chat.id,
        sender_id=bot_user_id,
        text=text,
        is_read=False,
    )
    session.add(msg)
    chat.last_message_at = datetime.now(timezone.utc)
    await session.flush()


async def handle_message(
    session: AsyncSession,
    chat_id: int,
    user_text: str | None,
) -> None:
    """Главный обработчик входящего сообщения пользователя."""
    if not user_text:
        return

    chat = await session.get(Chat, chat_id)
    if chat is None or not chat.bot_active or chat.chat_type == "notes":
        return

    bot_user_id = await get_bot_user_id(session)
    if bot_user_id is None:
        return

    # Пользователь хочет оператора после предложения
    low = user_text.lower().strip()
    if chat.bot_no_count >= settings.bot_max_attempts:
        if any(w in low for w in ("да", "нужен", "позови", "хочу оператора", "operator")):
            chat.operator_requested = True
            chat.bot_active = False
            await _send_bot_message(
                session, chat, bot_user_id,
                "Понял, передаю вас оператору. Ожидайте — скоро с вами свяжутся.",
            )
            await session.commit()
            return
        elif any(w in low for w in ("нет", "не нужен", "не надо", "сам")):
            chat.bot_no_count = 0
            chat.follow_up_sent = False
            await _send_bot_message(
                session, chat, bot_user_id,
                "Хорошо! Если возникнут вопросы — я здесь. Чем ещё могу помочь?",
            )
            await session.commit()
            return

    # Получаем последние сообщения для контекста диалога (до 6)
    history_rows = (await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id, Message.deleted_at.is_(None))
        .order_by(Message.id.desc())
        .limit(6)
    )).scalars().all()
    history = list(reversed(history_rows))

    # RAG: ищем релевантные куски
    context_chunks = await _retrieve_context(session, user_text, chat.cabinet_id)
    if context_chunks:
        parts = [f"[{c['source']}]\n{c['content']}" for c in context_chunks]
        context_text = "\n---\n".join(parts)
    else:
        context_text = "Контекст не найден."

    # Формируем историю для GPT
    gpt_messages = []
    for h in history[:-1]:  # без последнего (это текущее сообщение)
        role = "assistant" if h.sender_id == bot_user_id else "user"
        if h.text:
            gpt_messages.append({"role": role, "text": h.text})

    gpt_messages.append({
        "role": "user",
        "text": f"Контекст из базы знаний:\n{context_text}\n\nВопрос пользователя: {user_text}",
    })

    system = _SYSTEM_PROMPT
    if chat.bot_no_count > 0:
        system += f"\n\nЭто попытка {chat.bot_no_count + 1} из {settings.bot_max_attempts}. Постарайся помочь точнее."

    try:
        answer = await yandex_service.complete(system, gpt_messages)
    except Exception:
        answer = "Извините, сейчас не могу ответить. Попробуйте позже или запросите оператора."

    # Обновляем счётчик если пользователь недоволен
    if _is_negative(user_text):
        chat.bot_no_count += 1
    else:
        chat.bot_no_count = 0

    chat.follow_up_sent = False

    # Если исчерпаны попытки — предлагаем оператора
    if chat.bot_no_count >= settings.bot_max_attempts:
        answer += "\n\nЯ пытался помочь несколько раз, но, похоже, проблема не решена. Хотите, чтобы я позвал оператора? (да / нет)"

    await _send_bot_message(session, chat, bot_user_id, answer)
    await session.commit()


async def send_follow_up(session: AsyncSession) -> None:
    """APScheduler job: отправляет follow-up в неактивные чаты."""
    from datetime import timedelta
    from sqlalchemy import and_

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.bot_follow_up_minutes)

    chats = (await session.execute(
        select(Chat).where(
            and_(
                Chat.bot_active == True,
                Chat.follow_up_sent == False,
                Chat.chat_type != "notes",
                Chat.last_user_message_at.isnot(None),
                Chat.last_user_message_at < cutoff,
            )
        )
    )).scalars().all()

    if not chats:
        return

    bot_user_id = await get_bot_user_id(session)
    if bot_user_id is None:
        return

    for chat in chats:
        await _send_bot_message(
            session, chat, bot_user_id,
            "Здравствуйте! Удалось ли решить вашу проблему? Если нет — я готов помочь.",
        )
        chat.follow_up_sent = True

    await session.commit()
