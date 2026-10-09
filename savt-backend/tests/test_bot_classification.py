"""Классификация реплик пользователя в чате с ботом Асей — разбор по словам,
а не по подстроке (см. комментарий в bot_service.py: "не помогло" содержит
"помог", но благодарностью не является). Чистая логика, без БД, без LLM — ровно
то место, где тонкие ошибки проще поймать тестом, а не вживую в переписке.
"""
import pytest

from app.services.bot_service import (
    _classify,
    _explicit_operator_request,
    _is_bare_operator_request,
    _operator_intent,
    _tokens,
)


# --- _tokens: нормализация ---

def test_tokens_lowercases_and_splits_words():
    assert _tokens("Привет, как дела?") == ["привет", "как", "дела"]


def test_tokens_collapses_elongated_letters():
    # "вызываааааай" — растянутое написание для эмоции, должно читаться как обычное слово
    assert _tokens("вызываааааай") == ["вызывай"]


def test_tokens_keeps_double_letters_untouched():
    # двойные буквы в русском — обычное дело ("касса"), ровно 2 подряд не схлопываем
    assert _tokens("касса") == ["касса"]


def test_tokens_normalizes_yo_to_ye():
    assert _tokens("ещё") == ["еще"]


# --- _classify: благодарность/жалоба ---

@pytest.mark.parametrize("text", ["спасибо", "Спасибо!", "ок", "работает", "получилось"])
def test_classify_positive_words(text):
    assert _classify(text) == "positive"


@pytest.mark.parametrize("text", ["нет", "не помогло", "не работает", "не заработало"])
def test_classify_negation_of_positive_word_is_negative(text):
    # "не помогло" содержит "помог" как подстроку, но благодарностью не является
    assert _classify(text) == "negative"


@pytest.mark.parametrize("text", ["проблема осталась", "все равно", "по прежнему", "не то", "так и"])
def test_classify_negative_phrases(text):
    assert _classify(text) == "negative"


def test_classify_negative_overrides_positive_in_same_message():
    # докстринг-пример: благодарность + жалоба в одном сообщении — это жалоба
    assert _classify("спасибо, но не работает") == "negative"


def test_classify_whole_word_match_not_substring():
    # "около" содержит "ок" подстрокой, но это не одобрение — не должно матчиться
    assert _classify("я стою около дома") is None


def test_classify_no_signal_returns_none():
    assert _classify("какая модель у этого шкафа") is None


def test_classify_empty_text_returns_none():
    assert _classify("") is None


# --- _operator_intent: ответ на предложение позвать оператора ---

@pytest.mark.parametrize("text", ["да", "ага", "нужно", "оператора"])
def test_operator_intent_want(text):
    assert _operator_intent(text) == "want"


@pytest.mark.parametrize("text", ["нет", "неа", "сам разберусь"])
def test_operator_intent_refuse(text):
    assert _operator_intent(text) == "refuse"


def test_operator_intent_negated_want_word_is_refuse():
    # докстринг-пример: отрицание после слова "оператор" всё равно перевешивает,
    # даже хотя "оператор" встретилось раньше по тексту
    assert _operator_intent("оператор не нужен") == "refuse"


def test_operator_intent_unrelated_text_returns_none():
    assert _operator_intent("какая гарантия у ШУ") is None


# --- _explicit_operator_request: незапрошенная прямая просьба ---

def test_explicit_request_with_operator_word():
    assert _explicit_operator_request("позовите оператора") is True


def test_explicit_request_bare_verb_without_operator_word():
    # "вызывай" без слова "оператор" в том же сообщении — тоже просьба
    assert _explicit_operator_request("вызывай") is True


def test_explicit_request_negated_verb_is_not_a_request():
    # отрицание учитывается в пределах двух слов перед триггером: "не" стоит
    # перед "вызывай", а не перед "оператора", а "оператора" само по себе тоже
    # триггер
    assert _explicit_operator_request("не вызывай оператора") is False


def test_explicit_request_negated_unprompted_statement():
    # если триггер — первое слово, учитывается и отрицание ПОСЛЕ него
    # ("не нужен"): "Оператор не нужен, я сам разберусь" — не просьба позвать
    # оператора
    assert _explicit_operator_request("Оператор не нужен, я сам разберусь") is False


def test_explicit_request_no_operator_words_at_all():
    assert _explicit_operator_request("у меня не работает АСУ") is False


# --- _is_bare_operator_request: просьба без реального вопроса ---

def test_bare_request_pure_filler_and_operator_words():
    assert _is_bare_operator_request("ай вызывай оператора мне") is True


def test_bare_request_single_verb():
    assert _is_bare_operator_request("вызывай") is True


def test_not_bare_when_real_content_present():
    # просьба оператора совмещена с реальным вопросом — не "пустая" просьба
    assert _is_bare_operator_request("не работает АСУ, позовите оператора") is False
