from sqlalchemy import ColumnElement, String, func, literal, or_

LIKE_ESCAPE_CHAR = "\\"

# Ниже которого триграммное сходство считается "непохоже" (стандартный порог pg_trgm)
FUZZY_SIMILARITY_THRESHOLD = 0.3


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

    parts = []
    for col in columns:
        norm_col = func.normalize_search_text(col, type_=String)
        parts.append(norm_col.like(pattern))
        parts.append(func.similarity(norm_col, norm_query) >= threshold)
    return or_(*parts)
