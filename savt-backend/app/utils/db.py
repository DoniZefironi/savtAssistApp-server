import re
from datetime import date

from sqlalchemy import ColumnElement, String, and_, case, func, literal, or_, true

LIKE_ESCAPE_CHAR = "\\"

# Ниже которого триграммное сходство считается "непохоже" (стандартный порог pg_trgm)
FUZZY_SIMILARITY_THRESHOLD = 0.3

# У строки короче этого триграмм почти нет — например, у "100" их 1-2 с учётом
# паддинга, и почти любая другая строка с похожими цифрами рядом перепрыгивает
# порог 0.3 без реального совпадения (искали "100", получили что-то совсем
# другое просто потому, что где-то в номере телефона контакта тоже есть "100").
# Для таких запросов остаётся только точное вхождение подстроки, без похожести
_MIN_LENGTH_FOR_SIMILARITY = 4


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards (%, _) and the escape character itself."""
    return (
        value.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
        .replace("%", f"{LIKE_ESCAPE_CHAR}%")
        .replace("_", f"{LIKE_ESCAPE_CHAR}_")
    )


def fuzzy_condition(
    query: str, *columns: ColumnElement,
    threshold: float = FUZZY_SIMILARITY_THRESHOLD, allow_typos: bool | None = None,
):
    """OR-условие по колонкам, устойчивое к регистру/разделителям и опечаткам:
    - normalize_search_text() приводит обе стороны к нижнему регистру и заменяет
      "_"/"-"/повторные пробелы на один пробел — "ШУ_52К" и "шу 52к" совпадают;
    - similarity() (pg_trgm) находит опечатки вроде "вентелятор" -> "вентилятор"
      или "шк 52к" -> "шу 52к".
    Запрос с цифрами — это номер (ШУ, договор, телефон): "26_204_1" не должно
    находить соседний "26_205_1", хоть они и почти совпадают по триграммам, —
    поэтому для него похожесть отключается, остаётся только вхождение подстроки.
    allow_typos=True/False переопределяет это правило явно.
    Требует миграцию a4b7c6d5e473 (расширение pg_trgm + normalize_search_text).
    """
    # "%" не несёт смысла в реальных данных (номер ШУ, ФИО и т.п.) — проще убрать,
    # чем городить экранирование внутри normalize_search_text
    clean_query = query.replace("%", "")

    # Несколько слов ("ШУ 26", "Сидоров Семён") — каждое слово должно найтись
    # хоть в какой-то из колонок, не обязательно в одной и не подряд: слова
    # запроса часто относятся к разным полям (тип и номер ШУ, имя и телефон)
    words = clean_query.split()
    if len(words) > 1:
        return and_(*[
            fuzzy_condition(word, *columns, threshold=threshold, allow_typos=allow_typos)
            for word in words
        ])
    norm_query = func.normalize_search_text(clean_query, type_=String)
    # Явная конкатенация ("%" || normalize_search_text(:query) || "%"), а не .contains(),
    # т.к. .contains() расcчитан на литерал, а не на результат другого SQL-выражения
    percent = literal("%", type_=String)
    pattern = percent.concat(norm_query).concat(percent)

    if allow_typos is None:
        allow_typos = not any(ch.isdigit() for ch in clean_query)
    use_similarity = allow_typos and len(clean_query.strip()) >= _MIN_LENGTH_FOR_SIMILARITY

    parts = []
    for col in columns:
        norm_col = func.normalize_search_text(col, type_=String)
        parts.append(norm_col.like(pattern))
        if use_similarity:
            parts.append(func.similarity(norm_col, norm_query) >= threshold)
    return or_(*parts)


def match_score(query: str, column: ColumnElement, weight: float = 1.0):
    """Насколько хорошо ОДНА колонка совпала с запросом, число 0..weight —
    для ORDER BY, отдельно от fuzzy_condition (тот только отбирает строки, не
    ранжирует их). Уровни: точное совпадение — weight, начинается с запроса —
    weight*0.8, содержит запрос — weight*0.5, только нечёткое (опечатка,
    запрос ≥ _MIN_LENGTH_FOR_SIMILARITY символов) — weight*0.2, нет
    совпадения — 0. weight — вес самой колонки: у поиска по нескольким полям
    разной важности (например, название проекта важнее телефона контакта)
    им нужно разное weight, чтобы совпадение в менее важном поле не
    перевешивало совпадение в более важном."""
    clean_query = query.replace("%", "")
    norm_query = func.normalize_search_text(clean_query, type_=String)
    norm_col = func.normalize_search_text(column, type_=String)
    percent = literal("%", type_=String)
    contains_pattern = percent.concat(norm_query).concat(percent)
    prefix_pattern = norm_query.concat(percent)

    whens = [
        (norm_col == norm_query, weight),
        (norm_col.like(prefix_pattern), weight * 0.8),
        (norm_col.like(contains_pattern), weight * 0.5),
    ]
    if len(clean_query.strip()) >= _MIN_LENGTH_FOR_SIMILARITY:
        whens.append((func.similarity(norm_col, norm_query) >= FUZZY_SIMILARITY_THRESHOLD, weight * 0.2))
    return case(*whens, else_=0.0)


def any_of(*conditions) -> ColumnElement:
    """or_ без пустых (None) условий — подписи/даты подходят не каждому слову."""
    return or_(*[c for c in conditions if c is not None])


def words_condition(query: str, build) -> ColumnElement:
    """Каждое слово запроса должно подойти хоть чем-то: build(слово) возвращает
    условие для одного слова (обычно fuzzy_condition по колонкам + совпадение
    с подписью типа/статуса через or_). Нужен спискам, где слово может быть
    подписью, а не текстом поля — "ШУ 26": "ШУ" — тип, "26" — номер."""
    words = query.replace("%", "").split()
    return and_(true(), *[build(word) for word in words])


_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?$|^(\d{4})-(\d{2})-(\d{2})$")

# Даты в админке показываются по местному времени, а в БД лежат в UTC
LOCAL_TZ = "Europe/Minsk"


def date_condition(word: str, *columns: tuple[ColumnElement, bool]) -> ColumnElement | None:
    """Совпадение слова-даты ("29.09.2026", "29.09.26", "29.09", "2026-09-29")
    с любой из колонок. columns — пары (колонка, это_datetime_с_часовым_поясом).
    Не дата — None. Без года ищется любой год: "29.09" — день и месяц."""
    m = _DATE_RE.match(word.strip())
    if not m:
        return None
    if m.group(4):
        day, month, year = int(m.group(6)), int(m.group(5)), int(m.group(4))
    else:
        day, month = int(m.group(1)), int(m.group(2))
        raw_year = m.group(3)
        year = None if raw_year is None else (2000 + int(raw_year) if len(raw_year) == 2 else int(raw_year))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if year is not None:
        try:
            target = date(year, month, day)
        except ValueError:
            return None
    parts = []
    for col, is_datetime in columns:
        local = func.timezone(LOCAL_TZ, col) if is_datetime else col
        if year is not None:
            parts.append(func.date(local) == target)
        else:
            parts.append(and_(func.extract("day", local) == day, func.extract("month", local) == month))
    return or_(*parts)
