"""Сервис бота Аси: контекст из БД (проект, шкафы, область документов), поиск по
эмбеддингам с отсевом закрытых документов, отправка сообщений, обращение к
операторам и сама handle_message со всеми ветками — решённая проблема,
предложение оператора, настойчивая просьба, опрос при сбое Yandex, счётчик
попыток, follow-up. Разбор реплик по словам — в test_bot_classification.py.
Yandex (эмбеддинги и генерация), push и realtime подменяются и записываются."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.embedding import EMBEDDING_DIM, Embedding
from app.models.message import Message
from app.services import bot_service, push_service


def _vec(*head: float) -> list[float]:
    return list(head) + [0.0] * (EMBEDDING_DIM - len(head))


@pytest.fixture
def env(monkeypatch):
    e = SimpleNamespace(pushes=[], created=[], updated=[], completions=[], searched=[],
                        answer="Ответ бота", fail=False)

    async def push(session, user_id, title, body, data=None, notification_type=None):
        e.pushes.append(SimpleNamespace(user_id=user_id, title=title, body=body, type=notification_type))

    async def created(chat_id, payload):
        e.created.append(payload)

    async def updated(chat_id, summary):
        e.updated.append(summary)

    async def embed_query(text):
        e.searched.append(text)
        if e.fail:
            raise RuntimeError("Yandex недоступен")
        return _vec(1.0)

    async def complete(system, messages):
        if e.fail:
            raise RuntimeError("Yandex недоступен")
        e.completions.append(SimpleNamespace(system=system, messages=messages))
        return e.answer

    monkeypatch.setattr(push_service, "send_push", push)
    monkeypatch.setattr(bot_service, "publish_message_created", created)
    monkeypatch.setattr(bot_service, "publish_chat_updated", updated)
    monkeypatch.setattr(bot_service.yandex_service, "embed_query", embed_query)
    monkeypatch.setattr(bot_service.yandex_service, "complete", complete)
    monkeypatch.setattr(settings, "bot_max_attempts", 3)
    monkeypatch.setattr(settings, "bot_history_limit", 25)
    return e


@pytest.fixture
async def bot_id(db_session):
    return await bot_service.ensure_bot_user(db_session)


@pytest.fixture
def say(db_session):
    """Сообщение пользователя в чат — как его записывает send_message до вызова бота."""
    async def _say(chat, user, text):
        msg = Message(chat_id=chat.id, sender_id=user.id, text=text)
        db_session.add(msg)
        await db_session.flush()
        return msg
    return _say


async def _bot_texts(db_session, chat, bot_id):
    rows = (await db_session.execute(
        select(Message).where(Message.chat_id == chat.id, Message.sender_id == bot_id).order_by(Message.id)
    )).scalars()
    return [m.text for m in rows]


# --- учётка бота ---

async def test_bot_user_is_created_once_with_bot_role(db_session):
    from app.models.role import Role
    from app.models.user import User

    first = await bot_service.ensure_bot_user(db_session)
    second = await bot_service.ensure_bot_user(db_session)

    bot = await db_session.get(User, first)
    role = await db_session.get(Role, bot.role_id)
    assert first == second == await bot_service.get_bot_user_id(db_session)
    assert (bot.full_name, role.name, bot.is_active) == ("Ася", "bot", True)


async def test_bot_user_role_is_restored(db_session, make_user):
    from app.models.role import Role
    from app.models.user import User

    bot_id = await bot_service.ensure_bot_user(db_session)
    bot = await db_session.get(User, bot_id)
    bot.role_id = (await db_session.execute(select(Role.id).where(Role.name == "user"))).scalar_one()

    assert await bot_service.ensure_bot_user(db_session) == bot_id
    assert (await db_session.get(Role, bot.role_id)).name == "bot"


# --- область проекта и контекст из БД ---

async def test_project_scope_includes_nested_projects_and_their_cabinets(db_session, make_project, make_cabinet):
    root = await make_project()
    child = await make_project(parent_project_id=root.id)
    grandchild = await make_project(parent_project_id=child.id)
    await make_project(parent_project_id=root.id, deleted_at=datetime.now(timezone.utc))
    unrelated = await make_project()
    in_root = await make_cabinet(project_id=root.id)
    in_grandchild = await make_cabinet(project_id=grandchild.id)
    await make_cabinet(project_id=unrelated.id)
    await make_cabinet(project_id=root.id, deleted_at=datetime.now(timezone.utc))

    project_ids, cabinet_ids = await bot_service._resolve_project_scope(db_session, root.id)

    assert project_ids == {root.id, child.id, grandchild.id}
    assert cabinet_ids == {in_root.id, in_grandchild.id}


async def test_chat_project_is_resolved_from_chat_or_cabinet(db_session, make_user, make_chat, make_project, make_cabinet):
    user = await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    loose_cabinet = await make_cabinet()

    assert await bot_service._resolve_chat_project_id(db_session, await make_chat(user, "project", project_id=project.id)) == project.id
    assert await bot_service._resolve_chat_project_id(db_session, await make_chat(user, "cabinet", cabinet_id=cabinet.id)) == project.id
    assert await bot_service._resolve_chat_project_id(db_session, await make_chat(user, "cabinet", cabinet_id=loose_cabinet.id)) is None
    assert await bot_service._resolve_chat_project_id(db_session, await make_chat(user, "support")) is None


async def test_project_info_states_empty_fields_explicitly(db_session, make_project):
    empty = await make_project(name="Пустой")
    full = await make_project(
        name="Полный", production_number="26_100", company_name="ООО Ромашка",
        shipment_planned_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        shipment_actual_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        warranty_starts_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        warranty_ends_at=datetime(2027, 9, 5, tzinfo=timezone.utc),
    )

    text_empty = await bot_service._project_info_context(db_session, empty.id)
    text_full = await bot_service._project_info_context(db_session, full.id)

    assert "ещё не отгружено" in text_empty and "не указана" in text_empty and "НЕ УКАЗАНА" in text_empty
    for fragment in ("Название: Полный", "26_100", "ООО Ромашка", "2026-09-01", "да, 2026-09-05", "с 2026-09-05 до 2027-09-05"):
        assert fragment in text_full
    assert await bot_service._project_info_context(db_session, 999999) is None


async def test_cabinet_directory_hides_internal_comment(db_session, make_project, make_cabinet):
    project = await make_project()
    await make_cabinet(
        project_id=project.id, object_number="26_001", type="ШУ-18К", admin_internal_name="Насосная",
        purpose="Подкачка", description="Три насоса", latitude=53.9, longitude=27.5,
        admin_comment="ВНУТРЕННЯЯ ЗАМЕТКА", warranty_ends_at=datetime.now(timezone.utc) + timedelta(days=200),
    )
    await make_cabinet(project_id=project.id, object_number="26_002", deleted_at=datetime.now(timezone.utc))

    text = await bot_service._cabinet_directory_context(db_session, project.id)

    assert "26_001 (ШУ-18К), Насосная — гарантия действует до" in text
    assert "назначение: Подкачка" in text and "описание: Три насоса" in text and "координаты: 53.9, 27.5" in text
    assert "ВНУТРЕННЯЯ ЗАМЕТКА" not in text and "26_002" not in text
    assert await bot_service._cabinet_directory_context(db_session, (await make_project()).id) is None


# --- поиск по эмбеддингам ---

async def _embed(db_session, source_type, source_id, content, vec, meta=None):
    db_session.add(Embedding(source_type=source_type, source_id=source_id, content=content,
                             embedding=vec, meta=meta or {}))
    await db_session.flush()


async def test_retrieval_for_cabinet_prefers_its_documents_and_hides_restricted(
    db_session, env, make_cabinet, make_document,
):
    cabinet, other = await make_cabinet(), await make_cabinet()
    open_doc = await make_document(cabinet_id=cabinet.id)
    secret_doc = await make_document(cabinet_id=cabinet.id, requires_approval=True)
    internal_doc = await make_document(cabinet_id=cabinet.id, is_internal=True)
    foreign_doc = await make_document(cabinet_id=other.id)
    await _embed(db_session, "document", open_doc.id, "Открытый документ", _vec(1.0), {"cabinet_id": cabinet.id, "title": "Паспорт"})
    await _embed(db_session, "document", secret_doc.id, "Закрытый документ", _vec(1.0), {"cabinet_id": cabinet.id})
    await _embed(db_session, "document", internal_doc.id, "Служебный документ", _vec(1.0), {"cabinet_id": cabinet.id})
    await _embed(db_session, "document", foreign_doc.id, "Чужой документ", _vec(1.0), {"cabinet_id": other.id})
    await _embed(db_session, "faq", 1, "Общий ответ FAQ", _vec(0.0, 1.0), {"title": "Как включить"})
    await _embed(db_session, "kb_article", 1, "Статья базы", _vec(0.0, 0.0, 1.0))

    chunks = await bot_service._retrieve_context(db_session, "вопрос", cabinet.id)

    contents = [c["content"] for c in chunks]
    assert contents[0] == "Открытый документ" and chunks[0]["source"] == "Документация ШУ: Паспорт"
    assert set(contents) == {"Открытый документ", "Общий ответ FAQ", "Статья базы"}
    assert {"FAQ: Как включить", "База знаний"} <= {c["source"] for c in chunks}


async def test_retrieval_for_project_covers_nested_projects(db_session, env, make_project, make_cabinet, make_document):
    root = await make_project()
    child = await make_project(parent_project_id=root.id)
    cabinet = await make_cabinet(project_id=child.id)
    stranger = await make_project()
    direct = await make_document(project_id=root.id)
    via_cabinet = await make_document(cabinet_id=cabinet.id)
    foreign = await make_document(project_id=stranger.id)
    for doc, text in ((direct, "Документ проекта"), (via_cabinet, "Документ шкафа"), (foreign, "Чужой проект")):
        await _embed(db_session, "document", doc.id, text, _vec(1.0))

    chunks = await bot_service._retrieve_context(db_session, "вопрос", None, project_id=root.id)

    assert {c["content"] for c in chunks} == {"Документ проекта", "Документ шкафа"}


async def test_retrieval_without_scope_uses_only_general_pool(db_session, env, make_cabinet, make_document):
    doc = await make_document(cabinet_id=(await make_cabinet()).id)
    await _embed(db_session, "document", doc.id, "Документ шкафа", _vec(1.0), {"cabinet_id": 1})
    await _embed(db_session, "faq", 1, "Ближе", _vec(1.0, 0.1))
    await _embed(db_session, "faq", 2, "Дальше", _vec(0.1, 1.0))

    chunks = await bot_service._retrieve_context(db_session, "вопрос", None)

    assert [c["content"] for c in chunks] == ["Ближе", "Дальше"]


# --- отправка сообщений и обращение к операторам ---

async def test_bot_message_is_stored_pushed_and_published(db_session, env, make_user, make_chat, bot_id):
    user = await make_user()
    chat = await make_chat(user, "support")

    await bot_service._send_bot_message(db_session, chat, bot_id, "Привет! " + "я" * 200)

    [msg] = (await db_session.execute(select(Message).where(Message.chat_id == chat.id))).scalars().all()
    assert msg.sender_id == bot_id and msg.is_read is False and chat.last_message_at is not None
    [push] = env.pushes
    assert (push.user_id, push.title, push.type) == (user.id, "Ася", "chat_message") and len(push.body) == 100
    assert env.created[0]["sender_name"] == "Ася" and env.created[0]["text"] == msg.text
    assert env.updated[0]["last_message_text"] == msg.text


async def test_only_active_operators_and_admins_are_notified(db_session, env, make_user):
    operator, admin = await make_user("operator"), await make_user("admin")
    await make_user("operator", is_active=False)
    await make_user("superadmin")
    await make_user()

    await bot_service._notify_operators(db_session, 42)

    mine = {p.user_id for p in env.pushes if p.user_id in (operator.id, admin.id)}
    assert mine == {operator.id, admin.id}
    assert all(p.type == "operator_requested" for p in env.pushes)
    assert any("#42" in p.body for p in env.pushes)


# --- handle_message: когда бот молчит ---

@pytest.mark.parametrize("kwargs", [
    {"chat_type": "notes"},
    {"chat_type": "service_request"},
    {"chat_type": "support", "bot_active": False},
])
async def test_bot_stays_silent(db_session, env, make_user, make_chat, say, bot_id, kwargs):
    user = await make_user()
    chat_type = kwargs.pop("chat_type")
    chat = await make_chat(user, chat_type, **kwargs)
    await say(chat, user, "Привет")

    await bot_service.handle_message(db_session, chat.id, "Привет")

    assert await _bot_texts(db_session, chat, bot_id) == [] and env.completions == []


async def test_empty_text_unknown_chat_and_missing_bot_user(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support")
    await say(chat, user, "Привет")

    await bot_service.handle_message(db_session, chat.id, None)
    await bot_service.handle_message(db_session, 999999, "Привет")
    if await bot_service.get_bot_user_id(db_session) is None:
        await bot_service.handle_message(db_session, chat.id, "Привет")

    assert env.completions == [] and env.pushes == []


# --- handle_message: обычный ответ ---

async def test_regular_answer_uses_context_and_history(
    db_session, env, make_user, make_chat, make_project, make_cabinet, say, bot_id,
):
    user = await make_user()
    project = await make_project(name="Космос", company_name="ООО Ромашка")
    cabinet = await make_cabinet(project_id=project.id, object_number="26_001")
    chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)
    await say(chat, user, "Здравствуйте, нужна помощь")
    db_session.add(Message(chat_id=chat.id, sender_id=bot_id, text="Чем могу помочь?"))
    await db_session.flush()
    await say(chat, user, "Какая гарантия на шкаф и когда отгрузка?")
    env.answer = "Гарантия до конца года."

    await bot_service.handle_message(db_session, chat.id, "Какая гарантия на шкаф и когда отгрузка?")

    [call] = env.completions
    assert call.system.startswith("Ты — помощник Ася") and "попытка" not in call.system
    assert [m["role"] for m in call.messages] == ["user", "assistant", "user"]
    prompt = call.messages[-1]["text"]
    assert "Данные проекта" in prompt and "ООО Ромашка" in prompt and "ШУ этого проекта" in prompt
    assert prompt.endswith("Вопрос пользователя: Какая гарантия на шкаф и когда отгрузка?")
    assert (await _bot_texts(db_session, chat, bot_id))[-1] == "Гарантия до конца года."
    assert chat.bot_no_count == 0 and chat.bot_active is True


async def test_support_chat_has_no_project_blocks(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support")
    await say(chat, user, "Как проверить гарантию?")

    await bot_service.handle_message(db_session, chat.id, "Как проверить гарантию?")

    prompt = env.completions[0].messages[-1]["text"]
    assert "Данные проекта" not in prompt and "ШУ этого проекта" not in prompt
    assert "Контекст не найден." in prompt


async def test_short_reply_is_searched_together_with_recent_questions(db_session, env, make_user, make_chat, say, bot_id):
    user = await make_user()
    chat = await make_chat(user, "support")
    await say(chat, user, "Какие характеристики у ШУ 123")
    db_session.add(Message(chat_id=chat.id, sender_id=bot_id, text="Уточните, пожалуйста"))
    await db_session.flush()
    await say(chat, user, "смотри в руководстве")
    await say(chat, user, "технические характеристики")

    await bot_service.handle_message(db_session, chat.id, "технические характеристики")

    assert env.searched == ["Какие характеристики у ШУ 123 смотри в руководстве технические характеристики"]
    assert env.completions[0].messages[-1]["text"].endswith("Вопрос пользователя: технические характеристики")


async def test_long_question_is_searched_as_is(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support")
    text = "Подскажите пожалуйста как настроить уставки давления на насосе"
    await say(chat, user, text)

    await bot_service.handle_message(db_session, chat.id, text)

    assert env.searched == [text]


# --- handle_message: решение проблемы ---

async def test_thanks_resolve_an_open_problem(db_session, env, make_user, make_chat, say, bot_id):
    user = await make_user()
    chat = await make_chat(user, "support", bot_no_count=2)
    await say(chat, user, "Спасибо, всё заработало")

    await bot_service.handle_message(db_session, chat.id, "Спасибо, всё заработало")

    assert chat.problem_status == "resolved" and chat.follow_up_sent is True and chat.bot_no_count == 0
    assert (await _bot_texts(db_session, chat, bot_id))[0].startswith("Рад, что удалось помочь")
    assert env.completions == []


async def test_thanks_in_a_resolved_chat_go_to_the_model(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support", problem_status="resolved")
    await say(chat, user, "Спасибо")

    await bot_service.handle_message(db_session, chat.id, "Спасибо")

    assert len(env.completions) == 1


async def test_complaint_counts_attempts_and_hints_the_model(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support", bot_no_count=1, follow_up_sent=True)
    await say(chat, user, "не помогло")

    await bot_service.handle_message(db_session, chat.id, "не помогло")

    assert chat.bot_no_count == 2 and chat.follow_up_sent is False
    assert "Это попытка 2 из 3" in env.completions[0].system  # подсказка считает уже прошедшие жалобы


async def test_neutral_message_resets_attempts_but_keeps_follow_up_flag(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support", bot_no_count=2, follow_up_sent=True)
    await say(chat, user, "Где находится кнопка пуск")

    await bot_service.handle_message(db_session, chat.id, "Где находится кнопка пуск")

    assert chat.bot_no_count == 0 and chat.follow_up_sent is True


# --- handle_message: оператор ---

async def test_after_max_attempts_the_bot_offers_an_operator(db_session, env, make_user, make_chat, say, bot_id):
    user = await make_user()
    chat = await make_chat(user, "support", bot_no_count=2)
    await say(chat, user, "не помогло")

    await bot_service.handle_message(db_session, chat.id, "не помогло")

    text = (await _bot_texts(db_session, chat, bot_id))[-1]
    assert text.startswith("Ответ бота") and "позвал оператора? (да / нет)" in text
    assert chat.bot_offered_operator is True and chat.bot_active is True


async def test_agreeing_to_the_offer_hands_the_chat_over(db_session, env, make_user, make_chat, say, bot_id):
    user, operator = await make_user(), await make_user("operator")
    chat = await make_chat(user, "support", bot_offered_operator=True)
    await say(chat, user, "да")

    await bot_service.handle_message(db_session, chat.id, "да")

    assert chat.operator_requested is True and chat.bot_active is False and chat.bot_offered_operator is False
    assert "передаю вас оператору" in (await _bot_texts(db_session, chat, bot_id))[-1]
    assert operator.id in [p.user_id for p in env.pushes] and env.completions == []


async def test_refusing_the_offer_returns_to_normal(db_session, env, make_user, make_chat, say, bot_id):
    user = await make_user()
    chat = await make_chat(user, "support", bot_offered_operator=True, bot_no_count=3, follow_up_sent=True)
    await say(chat, user, "нет, я сам")

    await bot_service.handle_message(db_session, chat.id, "нет, я сам")

    assert chat.operator_requested is False and chat.bot_active is True
    assert (chat.bot_no_count, chat.follow_up_sent, chat.bot_offered_operator) == (0, False, False)
    assert (await _bot_texts(db_session, chat, bot_id))[-1].startswith("Хорошо!")


async def test_ambiguous_reply_to_the_offer_is_a_new_question(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support", bot_offered_operator=True)
    await say(chat, user, "Лучше расскажите про давление на выходе")

    await bot_service.handle_message(db_session, chat.id, "Лучше расскажите про давление на выходе")

    assert chat.bot_offered_operator is False and chat.bot_active is True and len(env.completions) == 1


async def test_bare_operator_request_gets_a_fixed_reply_first(db_session, env, make_user, make_chat, say, bot_id):
    user = await make_user()
    chat = await make_chat(user, "support")
    await say(chat, user, "вызывай оператора")

    await bot_service.handle_message(db_session, chat.id, "вызывай оператора")

    assert chat.operator_insist_count == 1 and chat.operator_requested is False
    assert (await _bot_texts(db_session, chat, bot_id))[-1].startswith("Извините, может я всё-таки смогу")
    assert env.completions == []  # модель в этой ветке не зовётся


async def test_repeated_request_hands_over_to_operator(db_session, env, make_user, make_chat, say, bot_id):
    user, operator = await make_user(), await make_user("operator")
    chat = await make_chat(user, "support", operator_insist_count=1)
    await say(chat, user, "позовите оператора")

    await bot_service.handle_message(db_session, chat.id, "позовите оператора")

    assert chat.operator_requested is True and chat.bot_active is False and chat.operator_insist_count == 0
    assert operator.id in [p.user_id for p in env.pushes]
    assert any("настойчиво просит оператора" in p.body for p in env.pushes)


async def test_request_with_a_real_question_is_still_answered(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support")
    text = "не работает АСУ, позовите оператора"
    await say(chat, user, text)

    await bot_service.handle_message(db_session, chat.id, text)

    assert chat.operator_insist_count == 1 and len(env.completions) == 1 and chat.operator_requested is False


async def test_insistence_counter_resets_after_a_regular_answer(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support", operator_insist_count=1)
    await say(chat, user, "Как включить насос")

    await bot_service.handle_message(db_session, chat.id, "Как включить насос")

    assert chat.operator_insist_count == 0


# --- handle_message: сбой Yandex и опрос ---

async def test_yandex_outage_starts_a_step_by_step_intake(db_session, env, make_user, make_chat, say, bot_id):
    user = await make_user()
    chat = await make_chat(user, "support")
    await say(chat, user, "Что с насосом?")
    env.fail = True

    await bot_service.handle_message(db_session, chat.id, "Что с насосом?")

    assert chat.bot_down_intake_step == 1 and chat.operator_requested is False
    text = (await _bot_texts(db_session, chat, bot_id))[-1]
    assert "технические неполадки" in text and "1. Ваш вопрос по ШУ" in text


async def test_intake_asks_every_question_then_calls_an_operator(db_session, env, make_user, make_chat, say, bot_id):
    user, operator = await make_user(), await make_user("operator")
    chat = await make_chat(user, "support", bot_down_intake_step=1)

    for answer in ("по шкафу", "поломка", "срочно", "26_001", "не включается"):
        await say(chat, user, answer)
        await bot_service.handle_message(db_session, chat.id, answer)

    texts = await _bot_texts(db_session, chat, bot_id)
    assert [t[:2] for t in texts[:4]] == ["2.", "3.", "4.", "5."]
    assert texts[-1].startswith("Спасибо! Передаю вас оператору")
    assert (chat.bot_down_intake_step, chat.operator_requested, chat.bot_active) == (0, True, False)
    assert any(p.user_id == operator.id and p.title == "Бот недоступен" for p in env.pushes)
    assert env.completions == []  # во время опроса модель не вызывается


# --- follow-up ---

async def test_follow_up_goes_only_to_silent_open_chats(db_session, env, make_user, make_chat, bot_id):
    user = await make_user()
    old = datetime.now(timezone.utc) - timedelta(minutes=settings.bot_follow_up_minutes + 5)
    fresh = datetime.now(timezone.utc) - timedelta(minutes=1)
    due = await make_chat(user, "support", last_user_message_at=old)
    skipped = [
        await make_chat(user, "cabinet", last_user_message_at=fresh),
        await make_chat(user, "project", last_user_message_at=old, follow_up_sent=True),
        await make_chat(user, "cabinet", last_user_message_at=old, problem_status="resolved"),
        await make_chat(user, "cabinet", last_user_message_at=old, bot_active=False),
        await make_chat(user, "notes", last_user_message_at=old),
        await make_chat(user, "cabinet"),
    ]

    await bot_service.send_follow_up(db_session)

    assert due.follow_up_sent is True and len(await _bot_texts(db_session, due, bot_id)) == 1
    for chat in skipped:
        assert await _bot_texts(db_session, chat, bot_id) == []
    await bot_service.send_follow_up(db_session)  # повторный запуск второго письма не шлёт
    assert len(await _bot_texts(db_session, due, bot_id)) == 1


async def test_found_context_goes_into_the_prompt_with_its_source(db_session, env, make_user, make_chat, say):
    user = await make_user()
    chat = await make_chat(user, "support")
    await _embed(db_session, "faq", 1, "Гарантия — 24 месяца со дня отгрузки", _vec(1.0), {"title": "Срок гарантии"})
    await say(chat, user, "Какой срок гарантии на шкафы управления")

    await bot_service.handle_message(db_session, chat.id, "Какой срок гарантии на шкафы управления")

    prompt = env.completions[0].messages[-1]["text"]
    assert "[FAQ: Срок гарантии]\nГарантия — 24 месяца со дня отгрузки" in prompt
    assert "Контекст не найден." not in prompt
