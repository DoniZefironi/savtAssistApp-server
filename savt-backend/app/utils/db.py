from sqlalchemy import ColumnElement, String, case, func, literal, or_

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


def fuzzy_condition(query: str, *columns: ColumnElement, threshold: float = FUZZY_SIMILARITY_THRESHOLD):
    """OR-условие по колонкам, устойчивое к регистру/разделителям и опечаткам:
    - normalize_search_text() приводит обе стороны к нижнему регистру и заменяет
      "_"/"-"/повторные пробелы на один пробел — "ШУ_52К" и "шу 52к" совпадают;
    - similarity() (pg_trgm) находит опечатки вроде "вентелятор" -> "вентилятор"
      или "шк 52к" -> "шу 52к".
    Требует миграцию a4b7c6d5e473 (расширение pg_trgm + normalize_search_text).
    """
    # "%" не несёт смысла в реальных данных (номер ШУ, ФИО и т.п.) — проще убрать,
    # чем городить экранирование внутри normalize_search_text
    clean_query = query.replace("%", "")
    norm_query = func.normalize_search_text(clean_query, type_=String)
    # Явная конкатенация ("%" || normalize_search_text(:query) || "%"), а не .contains(),
    # т.к. .contains() расcчитан на литерал, а не на результат другого SQL-выражения
    percent = literal("%", type_=String)
    pattern = percent.concat(norm_query).concat(percent)

    use_similarity = len(clean_query.strip()) >= _MIN_LENGTH_FOR_SIMILARITY

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
