import re
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import BOT_USER_LOGIN as _BOT_USER_LOGIN
from app.models.chat import Chat
from app.models.embedding import Embedding
from app.models.message import Message
from app.services import yandex_service
from app.services.chat_service import chat_summary_dict
from app.services.realtime_events import publish_chat_updated, publish_message_created

_BOT_NAME = "Ася"

_SYSTEM_PROMPT = """Ты — помощник Ася, виртуальный ассистент сервисной службы SAVT.
Ты помогаешь пользователям с вопросами по шкафам управления (ШУ): гарантия, документация, неисправности, обслуживание.

Правила:
- Отвечай чётко, кратко, структурированно.
- Используй только информацию из предоставленного контекста.
- Если в сообщении есть блок "ШУ этого проекта" — это точные данные из системы (не из базы знаний), используй их для вопросов о том, какие ШУ есть в проекте, и об их гарантии. Если конкретного ШУ в этом списке нет — так и скажи, не придумывай.
- Если контекст не содержит ответа — честно скажи об этом и задай уточняющий вопрос.
- Никогда не предлагай сама позвать/подключить оператора и не упоминай такую возможность — это решает не твой ответ, а система отдельно, вне зависимости от того, что ты написала. Если не можешь ответить — просто честно скажи об этом.
- Не придумывай информацию.
- Отвечай на русском языке.
- Обращайся к пользователю на "вы".
- Никогда не начинай ответ с приветствия ("Здравствуйте", "Добрый день", "Добрый вечер" и подобных) — диалог уже идёт, приветствие было в начале.
- Если в контексте есть фраза, подтверждающая твой ответ, — добавь её в конце в формате:
  > "цитата (1–2 предложения)"
  Если подходящей цитаты нет — не добавляй блок цитаты."""

# Разбор ответа пользователя идёт по СЛОВАМ, а не по вхождению подстроки.
# Раньше было `keyword in text.lower()`, и это ломалось на каждом шагу: "не
# помогло" содержит "помог", "не работает" содержит "работает" — то есть жалоба
# опознавалась как благодарность, и бот закрывал проблему как решённую. "ок"
# находилось внутри "блок", "около", "ток"; "нет" — внутри "интернет".
_WORD_RE = re.compile(r"[a-zа-я0-9]+")

# "не" перед положительным словом переворачивает смысл — так одно правило
# закрывает и "не помогло", и "не работает", и "не заработало"
_NEGATIONS = {"не", "ни"}

_POSITIVE_WORDS = {
    "спасибо", "благодарю", "спс", "решено", "решил", "решила", "решилось",
    "работает", "заработал", "заработало", "помогло", "помог", "помогла",
    "разобрался", "разобралась", "понял", "поняла", "получилось",
    "ок", "окей", "ага", "отлично", "супер", "норм",
}
_NEGATIVE_WORDS = {"нет", "неа", "непонятно"}
_NEGATIVE_PHRASES = (
    ("проблема", "осталась"), ("все", "равно"), ("по", "прежнему"),
    ("не", "то"), ("так", "и"),
)

# Ответы на предложение позвать оператора (контекстные — осмысленны только
# как реакция на явный вопрос бота "хотите оператора? да/нет", отсюда в
# списке есть и бытовые "да"/"нужно")
_WANT_OPERATOR_WORDS = {
    "да", "ага", "нужен", "нужно", "надо", "позови", "позовите", "зови",
    "оператор", "оператора", "operator",
}
_REFUSE_OPERATOR_WORDS = {"нет", "неа", "сам", "сама", "самостоятельно"}

# Незапрошенная (бот НЕ предлагал) просьба оператора — только явные слова.
# Отдельно от _WANT_OPERATOR_WORDS специально: там есть "да"/"нужно", которые
# вне контекста прямого вопроса бота ничего не значат — обычное "да" в ответ
# на любой другой вопрос бота не должно читаться как просьба позвать оператора
_EXPLICIT_OPERATOR_WORDS = {
    "оператор", "оператора", "оператору", "operator", "человека", "специалиста",
}

# Сколько раз бот настаивает на своей помощи в ответ на незапрошенную прямую
# просьбу оператора, прежде чем передать чат
_OPERATOR_INSIST_LIMIT = 1

# Слова самой просьбы ("вызывай(те)", "позови(те)" и т.п.) — вместе с
# _EXPLICIT_OPERATOR_WORDS используются, чтобы отличить просьбу оператора БЕЗ
# реального вопроса ("вызывай оператора") от совмещённой с вопросом ("не
# работает АСУ, позовите оператора"), см. _is_bare_operator_request
_OPERATOR_REQUEST_FILLER_WORDS = _EXPLICIT_OPERATOR_WORDS | {
    "вызывай", "вызови", "вызовите", "зови", "позови", "позовите",
    "подключи", "подключите", "переключи", "переключите",
    "хочу", "нужен", "нужно", "надо", "дай", "дайте", "живого", "живой",
    "пожалуйста",
}


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower().replace("ё", "е"))


def _has_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return any(tuple(tokens[i:i + n]) == phrase for i in range(len(tokens) - n + 1))


async def get_bot_user_id(session: AsyncSession) -> int | None:
    from app.models.user import User
    result = await session.execute(
        select(User).where(User.login == _BOT_USER_LOGIN)
    )
    user = result.scalar_one_or_none()
    return user.id if user else None


async def ensure_bot_user(session: AsyncSession) -> int:
    from app.models.role import Role
    from app.models.user import User
    from app.core.security import hash_password
    import secrets

    bot_role = (await session.execute(
        select(Role).where(Role.name == "bot")
    )).scalar_one_or_none()
    if bot_role is None:
        raise RuntimeError("Роль 'bot' не найдена в БД — примените миграции")

    result = await session.execute(
        select(User).where(User.login == _BOT_USER_LOGIN)
    )
    bot = result.scalar_one_or_none()
    if bot:
        if bot.role_id != bot_role.id:
            bot.role_id = bot_role.id
            await session.commit()
        return bot.id

    bot = User(
        login=_BOT_USER_LOGIN,
        full_name=_BOT_NAME,
        hashed_password=hash_password(secrets.token_hex(32)),
        role_id=bot_role.id,
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


# Сколько кусков документации ШУ/проекта берём максимум — оставшееся до k
# добирается из общей базы (FAQ/КБ). Раньше было 4, подняли до 6 вместе с k,
# чтобы бот реже отвечал "не нашёл", особенно когда область поиска расширилась
# на весь проект целиком (см. _resolve_project_scope)
_SCOPED_DOCS_LIMIT = 6


async def _resolve_project_scope(session: AsyncSession, project_id: int) -> tuple[set[int], set[int]]:
    """Проект + все его дочерние проекты (рекурсивно, вложенность не ограничена
    одним уровнем) → (все project_id в области, все cabinet_id всех этих проектов).
    Так чат проекта видит документацию не только самого проекта и его ШУ, но и
    вложенных проектов (например, партий отгрузки внутри одного производственного)."""
    from app.models.cabinets import Cabinet as CabinetModel
    from app.models.project import Project as ProjectModel

    project_ids = {project_id}
    frontier = {project_id}
    while frontier:
        rows = (await session.execute(
            select(ProjectModel.id).where(
                ProjectModel.parent_project_id.in_(frontier), ProjectModel.deleted_at.is_(None),
            )
        )).scalars().all()
        new_ids = set(rows) - project_ids
        if not new_ids:
            break
        project_ids |= new_ids
        frontier = new_ids

    cabinet_rows = (await session.execute(
        select(CabinetModel.id).where(
            CabinetModel.project_id.in_(project_ids), CabinetModel.deleted_at.is_(None),
        )
    )).scalars().all()

    return project_ids, set(cabinet_rows)


# Ярлыки статуса гарантии — те же смыслы, что и warranty_status(), для текста
# в промпте бота (не для API-ответа, там значения самого warranty_status как есть)
_WARRANTY_STATUS_LABELS = {
    "active": "гарантия действует",
    "expiring_soon": "гарантия скоро истекает",
    "expired": "гарантия истекла",
    "none": "гарантии нет",
}


async def _cabinet_directory_context(session: AsyncSession, chat: Chat) -> str | None:
    """Список ШУ проекта (номер, тип, гарантия) — структурированные данные из
    БД, которых нет и не может быть в базе знаний через RAG-поиск (вопросы вида
    "есть ли ШУ-318 в этом проекте", "когда гарантия на ШУ-52"). Для чата
    проекта — все ШУ проекта; для чата конкретного ШУ — он сам плюс остальные
    ШУ того же проекта (через cabinet.project_id). Для чата без привязки к
    проекту (support, либо ШУ вне проекта) — ничего не возвращаем: бот честно
    не знает, о каком проекте речь, лучше молчать, чем придумывать."""
    from app.models.cabinets import Cabinet as CabinetModel
    from app.utils.warranty import warranty_status

    project_id = chat.project_id
    if project_id is None and chat.cabinet_id is not None:
        cabinet = await session.get(CabinetModel, chat.cabinet_id)
        project_id = cabinet.project_id if cabinet else None
    if project_id is None:
        return None

    cabinets = (await session.execute(
        select(CabinetModel)
        .where(CabinetModel.project_id == project_id, CabinetModel.deleted_at.is_(None))
        .order_by(CabinetModel.object_number)
    )).scalars().all()
    if not cabinets:
        return None

    lines = []
    for c in cabinets:
        status = _WARRANTY_STATUS_LABELS[warranty_status(c.warranty_ends_at)]
        name_suffix = f", {c.admin_internal_name}" if c.admin_internal_name else ""
        until = f" до {c.warranty_ends_at.date().isoformat()}" if c.warranty_ends_at else ""
        lines.append(f"- {c.object_number} ({c.type}){name_suffix} — {status}{until}")
    return "ШУ этого проекта (точные данные из системы, не из базы знаний):\n" + "\n".join(lines)


async def _retrieve_context(
    session: AsyncSession, query: str, cabinet_id: int | None, project_id: int | None = None, k: int = 7,
) -> list[dict]:
    from sqlalchemy import or_
    from app.models.document import Document as DocumentModel
    vec = cast(await yandex_service.embed_query(query), Vector(256))

    # Подзапрос: ID документов с ограниченным или служебным доступом (не давать боту).
    # is_internal сюда попадает для подстраховки — сами эмбеддинги для таких
    # документов и не должны существовать (см. bot_indexer.index_document), но
    # если индексация когда-то отстала/сбойнула, этот фильтр не даст утечки.
    restricted_ids = select(DocumentModel.id).where(
        or_(DocumentModel.requires_approval == True, DocumentModel.is_internal == True)
    ).scalar_subquery()

    # Общий пул: только FAQ и KB (без документов конкретных ШУ/проектов)
    general_stmt = (
        select(Embedding.content, Embedding.source_type, Embedding.meta)
        .where(Embedding.source_type.in_(["faq", "kb_article"]))
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
                ~Embedding.source_id.in_(restricted_ids),
            )
            .order_by(Embedding.embedding.op("<=>")(vec))
            .limit(_SCOPED_DOCS_LIMIT)
        )
        cabinet_rows = (await session.execute(cabinet_stmt)).all()
        remaining = k - len(cabinet_rows)
        general_rows = (await session.execute(general_stmt.limit(remaining))).all() if remaining > 0 else []
        return [_row_to_dict(r) for r in cabinet_rows + general_rows]

    if project_id:
        project_ids, cabinet_ids = await _resolve_project_scope(session, project_id)
        # meta документа не хранит project_id (только cabinet_id, см. bot_indexer.index_document),
        # поэтому здесь JOIN прямо на Document, а не фильтр по meta
        project_stmt = (
            select(Embedding.content, Embedding.source_type, Embedding.meta)
            .join(DocumentModel, DocumentModel.id == Embedding.source_id)
            .where(
                Embedding.source_type == "document",
                or_(
                    DocumentModel.project_id.in_(project_ids),
                    DocumentModel.cabinet_id.in_(cabinet_ids),
                ),
                ~Embedding.source_id.in_(restricted_ids),
            )
            .order_by(Embedding.embedding.op("<=>")(vec))
            .limit(_SCOPED_DOCS_LIMIT)
        )
        project_rows = (await session.execute(project_stmt)).all()
        remaining = k - len(project_rows)
        general_rows = (await session.execute(general_stmt.limit(remaining))).all() if remaining > 0 else []
        return [_row_to_dict(r) for r in project_rows + general_rows]

    return [_row_to_dict(r) for r in (await session.execute(general_stmt)).all()]


def _classify(text: str) -> str | None:
    """"positive" | "negative" | None. Негатив всегда перевешивает: во фразе
    "спасибо, но не работает" человек недоволен, а не благодарит."""
    tokens = _tokens(text)
    if not tokens:
        return None

    if any(_has_phrase(tokens, p) for p in _NEGATIVE_PHRASES):
        return "negative"

    has_positive = False
    for i, token in enumerate(tokens):
        if token in _POSITIVE_WORDS:
            if i > 0 and tokens[i - 1] in _NEGATIONS:
                return "negative"
            has_positive = True

    if any(token in _NEGATIVE_WORDS for token in tokens):
        return "negative"
    return "positive" if has_positive else None


def _operator_intent(text: str) -> str | None:
    """"want" | "refuse" | None — ответ на предложение позвать оператора.

    Отрицание учитывается так же, как в _classify, и так же перевешивает: во
    фразе "оператор не нужен" слово "оператор" встречается раньше отрицания,
    поэтому выходить на первом совпадении нельзя — надо дочитать до конца."""
    tokens = _tokens(text)
    wants = False
    for i, token in enumerate(tokens):
        negated = i > 0 and tokens[i - 1] in _NEGATIONS
        if token in _WANT_OPERATOR_WORDS:
            if negated:
                return "refuse"
            wants = True
        elif token in _REFUSE_OPERATOR_WORDS:
            return "refuse"
    return "want" if wants else None


def _explicit_operator_request(text: str) -> bool:
    """Пользователь сам, без вопроса от бота, явно просит оператора — только
    по явным словам ("оператор" и т.п.), см. _EXPLICIT_OPERATOR_WORDS."""
    tokens = _tokens(text)
    for i, token in enumerate(tokens):
        if token in _EXPLICIT_OPERATOR_WORDS:
            negated = i > 0 and tokens[i - 1] in _NEGATIONS
            if not negated:
                return True
    return False


def _is_bare_operator_request(text: str) -> bool:
    """True, если в сообщении нет ничего, кроме самой просьбы позвать
    оператора — реального вопроса нет, гонять через RAG/LLM бессмысленно
    (модель честно отвечает "не могу вызвать оператора", что вместе с
    настойчивой припиской бота звучит как противоречие). См. handle_message."""
    tokens = _tokens(text)
    remaining = [t for t in tokens if t not in _OPERATOR_REQUEST_FILLER_WORDS]
    return len(remaining) == 0


def _is_negative(text: str) -> bool:
    return _classify(text) == "negative"


def _is_positive(text: str) -> bool:
    return _classify(text) == "positive"


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
    await session.refresh(msg)
    from app.services.push_service import send_push
    await send_push(
        session, chat.user_id, _BOT_NAME, text[:100],
        {"chat_id": str(chat.id), "type": "chat_message"},
        notification_type="chat_message",
    )

    message_payload = {
        "id": msg.id,
        "chat_id": msg.chat_id,
        "sender_id": msg.sender_id,
        "sender_name": _BOT_NAME,
        "text": msg.text,
        "reply_to_message_id": msg.reply_to_message_id,
        "is_read": msg.is_read,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "edited_at": None,
        "deleted_at": None,
        "attachments": [],
        "reactions": [],
    }
    await publish_message_created(chat.id, message_payload)
    await publish_chat_updated(chat.id, chat_summary_dict(chat, msg.text))


async def _notify_operators(
    session: AsyncSession, chat_id: int, title: str = "Запрос оператора", body: str | None = None
) -> None:
    from app.models.role import Role
    from app.models.user import User
    from app.services.push_service import send_push

    operators = (await session.execute(
        select(User)
        .join(Role, Role.id == User.role_id)
        .where(Role.name.in_(["operator", "admin"]), User.is_active == True)
    )).scalars().all()

    for op in operators:
        await send_push(
            session, op.id,
            title,
            body or f"Пользователь ожидает оператора в чате #{chat_id}",
            {"chat_id": str(chat_id), "type": "operator_requested"},
            # служебный сигнал операторам — настройками пользователя не выключается
            notification_type="operator_requested",
        )


_BOT_DOWN_QUESTIONS = (
    "Извините, сейчас не могу ответить — технические неполадки на моей стороне. "
    "Я уже позвал оператора, но чтобы он разобрался быстрее, ответьте, пожалуйста, на несколько вопросов:\n\n"
    "1. Ваш вопрос по ШУ (шкафу управления) или общий?\n"
    "2. Если по ШУ — это неполадка/поломка или вы хотите оставить заявку на обслуживание?\n"
    "3. Ситуация срочная (авария) или можно подождать?\n"
    "4. Укажите, пожалуйста, номер объекта/ШУ, если вопрос касается конкретного оборудования.\n"
    "5. Кратко опишите суть вопроса одним сообщением — так оператору не придётся переспрашивать."
)


async def handle_message(
    session: AsyncSession,
    chat_id: int,
    user_text: str | None,
) -> None:
    if not user_text:
        return

    chat = await session.get(Chat, chat_id)
    if chat is None or not chat.bot_active or chat.chat_type in ("notes", "service_request"):
        return

    bot_user_id = await get_bot_user_id(session)
    if bot_user_id is None:
        return

    sentiment = _classify(user_text)

    # Проблема решена — пользователь доволен
    if sentiment == "positive" and chat.problem_status == "open":
        chat.problem_status = "resolved"
        chat.follow_up_sent = True
        chat.bot_no_count = 0
        await _send_bot_message(
            session, chat, bot_user_id,
            "Рад, что удалось помочь! Если возникнут новые вопросы — обращайтесь.",
        )
        await session.commit()
        return

    # Бот только что САМ явно предложил оператора (готовой фразой после
    # исчерпания попыток) — ответ трактуется безусловно, а не по счётчику
    # bot_no_count. Раньше проверка была завязана именно на счётчик, а LLM
    # могла упомянуть оператора в своём ответе раньше, чем счётчик дорастал
    # до порога — согласие пользователя терялось и уходило в RAG как новый
    # вопрос (отсюда и системному промпту теперь явно запрещено предлагать
    # оператора самой, см. _SYSTEM_PROMPT — предложение всегда идёт только
    # отсюда, детерминированно).
    if chat.bot_offered_operator:
        chat.bot_offered_operator = False
        operator_answer = _operator_intent(user_text)
        if operator_answer == "want":
            chat.operator_requested = True
            chat.bot_active = False
            await _send_bot_message(
                session, chat, bot_user_id,
                "Понял, передаю вас оператору. Ожидайте — скоро с вами свяжутся.",
            )
            await session.commit()
            await _notify_operators(session, chat.id)
            return
        elif operator_answer == "refuse":
            chat.bot_no_count = 0
            chat.follow_up_sent = False
            await _send_bot_message(
                session, chat, bot_user_id,
                "Хорошо! Если возникнут вопросы — я здесь. Чем ещё могу помочь?",
            )
            await session.commit()
            return
        # неоднозначный ответ (не да, не нет) — трактуем как новое сообщение
        # (например, сразу новый вопрос) и идём в обычный цикл ниже

    # Незапрошенная просьба оператора (бот сам не предлагал) — не передаём
    # сразу, настаиваем на своей помощи первые _OPERATOR_INSIST_LIMIT раз.
    # Само сообщение может содержать реальный вопрос ("не работает АСУ,
    # позовите оператора") — на него всё равно отвечаем как обычно ниже,
    # просто добавляем настойчивую приписку; счётчик пользователю не виден
    insisting = False
    if _explicit_operator_request(user_text):
        if chat.operator_insist_count < _OPERATOR_INSIST_LIMIT:
            chat.operator_insist_count += 1
            if _is_bare_operator_request(user_text):
                # Нечего отвечать по существу — не гоняем "вопрос" вроде
                # "вызывай оператора" через RAG/LLM (модель честно скажет,
                # что не может вызвать оператора сама, а это только запутает
                # рядом с настойчивой припиской бота). Фиксированный ответ,
                # без LLM
                await _send_bot_message(
                    session, chat, bot_user_id,
                    "Конечно! Но для начала давайте я попробую помочь — опишите, пожалуйста, что случилось.",
                )
                await session.commit()
                return
            insisting = True
        else:
            chat.operator_requested = True
            chat.bot_active = False
            chat.operator_insist_count = 0
            await _send_bot_message(
                session, chat, bot_user_id,
                "Хорошо, передаю вас оператору. Ожидайте — скоро с вами свяжутся.",
            )
            await session.commit()
            await _notify_operators(
                session, chat.id,
                title="Пользователь просит оператора",
                body=f"Пользователь настойчиво просит оператора в чате #{chat.id}",
            )
            return

    try:
        # Получаем последние сообщения для контекста диалога (до 6)
        history_rows = (await session.execute(
            select(Message)
            .where(Message.chat_id == chat_id, Message.deleted_at.is_(None))
            .order_by(Message.id.desc())
            .limit(6)
        )).scalars().all()
        history = list(reversed(history_rows))

        # Короткий ответ ("ШУ-52", "да", "12") сам по себе почти не несёт
        # смысла для векторного поиска — это ответ НА уточняющий вопрос бота,
        # а не новый вопрос. Без этого поиск шёл по одному "ШУ-52", ничего не
        # находил, и бот "терял" тему разговора (спрашивал про красную кнопку,
        # уточнил модель — а бот снова "чем могу помочь"), хотя история и
        # передавалась модели: контекст диалога у неё был, а вот сам поиск по
        # базе знаний — нет. Склеиваем с последним реальным вопросом
        # пользователя ТОЛЬКО для поиска — в сам промпт уходит текст как есть
        search_query = user_text
        if len(_tokens(user_text)) <= 4:
            prev_user_text = next(
                (h.text for h in reversed(history[:-1]) if h.sender_id != bot_user_id and h.text),
                None,
            )
            if prev_user_text:
                search_query = f"{prev_user_text} {user_text}"

        # RAG: ищем релевантные куски
        context_chunks = await _retrieve_context(session, search_query, chat.cabinet_id, chat.project_id)
        if context_chunks:
            parts = [f"[{c['source']}]\n{c['content']}" for c in context_chunks]
            context_text = "\n---\n".join(parts)
        else:
            context_text = "Контекст не найден."

        # Список ШУ проекта — отдельно от RAG, это структурированные данные из
        # БД (см. _cabinet_directory_context), не найдётся никаким поиском по
        # документам. None для чата поддержки/ШУ вне проекта — тогда просто
        # не добавляем блок вообще
        directory_context = await _cabinet_directory_context(session, chat)

        # Формируем историю для GPT
        gpt_messages = []
        for h in history[:-1]:  # без последнего (это текущее сообщение)
            role = "assistant" if h.sender_id == bot_user_id else "user"
            if h.text:
                gpt_messages.append({"role": role, "text": h.text})

        prompt_text = f"Контекст из базы знаний:\n{context_text}"
        if directory_context:
            prompt_text = f"{directory_context}\n\n{prompt_text}"
        prompt_text += f"\n\nВопрос пользователя: {user_text}"
        gpt_messages.append({"role": "user", "text": prompt_text})

        system = _SYSTEM_PROMPT
        if chat.bot_no_count > 0:
            system += f"\n\nЭто попытка {chat.bot_no_count + 1} из {settings.bot_max_attempts}. Постарайся помочь точнее."

        answer = await yandex_service.complete(system, gpt_messages)
    except Exception:
        # Любой сбой Yandex API (эмбеддинги или генерация): ключ невалиден,
        # закончились деньги, сеть недоступна и т.п. — не пытаемся угадать причину,
        # сразу передаём чат оператору с наводящими вопросами вместо тишины/дежурной фразы.
        chat.operator_requested = True
        chat.bot_active = False
        await _send_bot_message(session, chat, bot_user_id, _BOT_DOWN_QUESTIONS)
        await session.commit()
        await _notify_operators(
            session, chat.id,
            title="Бот недоступен",
            body=f"Бот не смог ответить в чате #{chat.id} — требуется оператор",
        )
        return

    # Обновляем счётчик если пользователь недоволен
    if sentiment == "negative":
        chat.bot_no_count += 1
        chat.follow_up_sent = False  # после негатива разрешаем ещё один follow-up
    else:
        chat.bot_no_count = 0
        # follow_up_sent не сбрасываем — бот не будет слать follow-up каждые N минут

    # Счётчик настойчивых просьб оператора сбрасывается, как только пользователь
    # получил обычный реальный ответ (без просьбы оператора в этом же сообщении) —
    # иначе одно случайное упоминание оператора когда-то раньше копилось бы вечно
    if not insisting and chat.operator_insist_count > 0:
        chat.operator_insist_count = 0

    if insisting:
        answer += "\n\nЕсли это не поможет — просто попросите оператора ещё раз, и я вас передам."

    # Если исчерпаны попытки — предлагаем оператора. Флаг, а не текст сам по
    # себе — именно по нему следующий ответ пользователя трактуется как
    # согласие/отказ безусловно (см. начало функции)
    if chat.bot_no_count >= settings.bot_max_attempts:
        answer += "\n\nЯ пытался помочь несколько раз, но, похоже, проблема не решена. Хотите, чтобы я позвал оператора? (да / нет)"
        chat.bot_offered_operator = True

    await _send_bot_message(session, chat, bot_user_id, answer)
    await session.commit()


async def send_follow_up(session: AsyncSession) -> None:
    from datetime import timedelta
    from sqlalchemy import and_

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.bot_follow_up_minutes)

    chats = (await session.execute(
        select(Chat).where(
            and_(
                Chat.bot_active == True,
                Chat.follow_up_sent == False,
                Chat.problem_status == "open",
                Chat.chat_type.notin_(("notes", "service_request")),
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
