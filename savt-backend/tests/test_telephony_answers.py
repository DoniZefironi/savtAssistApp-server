import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.models.embedding import EMBEDDING_DIM, Embedding
from app.models.faq_category import FaqCategory
from app.models.faq_entry import FaqEntry
from app.models.kb_article_attachment import KbArticleAttachment
from app.models.kbarticle import KbArticle
from app.models.kbcategory import KbCategory
from app.services import yandex_service


PATH = "/internal/telephony/answer-question"
HEADERS = {"X-Telephony-Secret": "sip-test-secret"}
QUESTION = "Где найти инструкцию по подключению шкафа?"
ANSWER = "Инструкция находится в разделе «Документы» приложения."
VECTOR = [1.0] + [0.0] * (EMBEDDING_DIM - 1)


@pytest.fixture
def ai(monkeypatch):
    monkeypatch.setattr(settings, "telephony_api_secret", HEADERS["X-Telephony-Secret"])
    embed = AsyncMock(return_value=VECTOR)
    complete = AsyncMock(return_value=ANSWER)
    monkeypatch.setattr(yandex_service, "embed_query", embed)
    monkeypatch.setattr(yandex_service, "complete", complete)
    return SimpleNamespace(embed=embed, complete=complete)


async def _faq(db_session, *, published=True):
    category = FaqCategory(name="Инструкции")
    db_session.add(category)
    await db_session.flush()
    entry = FaqEntry(category_id=category.id, question=QUESTION, answer=ANSWER, is_published=published)
    db_session.add(entry)
    await db_session.flush()
    db_session.add(Embedding(
        source_type="faq", source_id=entry.id, content="Устаревший ответ", embedding=VECTOR,
    ))
    await db_session.flush()


async def _kb(db_session, *, content=None, published=True):
    category = KbCategory(name="Подключение", slug="telephony-kb")
    db_session.add(category)
    await db_session.flush()
    article = KbArticle(
        category_id=category.id, title="Подключение шкафа", slug="telephony-connection",
        content=content, is_published=published,
    )
    db_session.add(article)
    await db_session.flush()
    db_session.add_all([
        KbArticleAttachment(
            article_id=article.id, file_url="/static/files/restricted.pdf", file_size_bytes=1,
            doc_type="pdf", mime_type="application/pdf", title="Закрытая инструкция", requires_approval=True,
        ),
        Embedding(
            source_type="kb_article", source_id=article.id, embedding=VECTOR,
            content="Текст закрытого вложения", meta={"title": "Заголовок закрытого вложения"},
        ),
    ])
    await db_session.flush()


async def test_answers_from_the_current_published_faq(api, db_session, ai):
    await _faq(db_session)

    response = await api.post(PATH, headers=HEADERS, json={"question": QUESTION})

    assert response.status_code == 200
    assert response.json() == {"status": "answered", "answer": ANSWER}
    ai.embed.assert_awaited_once_with(QUESTION)
    prompt = json.loads(ai.complete.call_args.args[1][0]["text"])
    assert prompt == {"question": QUESTION, "materials": [f"FAQ: {QUESTION}\n{ANSWER}"]}


async def test_no_materials_returns_needs_operator_without_generation(api, ai):
    response = await api.post(PATH, headers=HEADERS, json={"question": "Как заказать доставку на Марс?"})

    assert response.status_code == 200
    assert response.json() == {"status": "needs_operator", "answer": None}
    ai.complete.assert_not_awaited()


async def test_only_restricted_attachment_returns_needs_operator(api, db_session, ai):
    await _kb(db_session)

    response = await api.post(PATH, headers=HEADERS, json={"question": QUESTION})

    assert response.status_code == 200
    assert response.json() == {"status": "needs_operator", "answer": None}
    ai.complete.assert_not_awaited()


async def test_published_article_uses_its_body_without_restricted_attachment(api, db_session, ai):
    await _kb(db_session, content=ANSWER)

    response = await api.post(PATH, headers=HEADERS, json={"question": QUESTION})

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    prompt = json.loads(ai.complete.call_args.args[1][0]["text"])
    assert prompt == {"question": QUESTION, "materials": [f"База знаний: Подключение шкафа\n{ANSWER}"]}


async def test_unpublished_sources_are_not_used_even_if_indexed(api, db_session, ai):
    await _faq(db_session, published=False)
    await _kb(db_session, content=ANSWER, published=False)

    response = await api.post(PATH, headers=HEADERS, json={"question": QUESTION})

    assert response.status_code == 200
    assert response.json() == {"status": "needs_operator", "answer": None}
    ai.complete.assert_not_awaited()


@pytest.mark.parametrize("question", ["До какого числа гарантия на мой шкаф?", "Как заменить насос?"])
async def test_model_falls_back_when_materials_do_not_answer_the_question(api, db_session, ai, question):
    await _faq(db_session)
    ai.complete.return_value = "[[НУЖЕН_ОПЕРАТОР]]"

    response = await api.post(PATH, headers=HEADERS, json={"question": question})

    assert response.status_code == 200
    assert response.json() == {"status": "needs_operator", "answer": None}
    ai.complete.assert_awaited_once()


async def test_no_secret_is_rejected_before_search(api, ai):
    response = await api.post(PATH, json={"question": QUESTION})

    assert response.status_code == 403
    ai.embed.assert_not_awaited()
    ai.complete.assert_not_awaited()


@pytest.mark.parametrize("stage", ["embed", "complete"])
async def test_search_or_model_failure_returns_5xx(api, db_session, ai, stage):
    await _faq(db_session)
    getattr(ai, stage).side_effect = RuntimeError("Внутренние подробности ошибки")

    response = await api.post(PATH, headers=HEADERS, json={"question": QUESTION})

    assert response.status_code == 503
    assert response.json() == {"detail": "Не удалось ответить на вопрос"}


async def test_non_voice_model_output_returns_5xx(api, db_session, ai):
    await _faq(db_session)
    ai.complete.return_value = "Первое. Второе. Третье. Четвёртое."

    response = await api.post(PATH, headers=HEADERS, json={"question": QUESTION})

    assert response.status_code == 503
