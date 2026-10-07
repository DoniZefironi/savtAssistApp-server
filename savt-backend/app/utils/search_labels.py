"""Подписи значений, которые админка показывает вместо кодов из БД: в базе
лежит `resolved`/`cabinet`, на экране — "Закрыта"/"ШУ", и ищут по тому, что
видят. Один код может иметь несколько подписей (синонимы)."""
from sqlalchemy import ColumnElement, or_

Labels = dict[str, str | tuple[str, ...]]

RECLAMATION_OBJECT_TYPE: Labels = {
    "cabinet": "ШУ", "line": "Линия", "component": "ПКИ", "software": "ПО", "documentation": "Документация",
}
RECLAMATION_STATUS: Labels = {
    "new": "Новая рекламация", "review": "На рассмотрении", "in_progress": "Принята в работу",
    "resolved": "Закрыта", "rejected": "Отклонена", "invalid": "Ошибочная",
}
# warranty_classification: True / False / NULL
RECLAMATION_WARRANTY: dict[bool | None, str] = {
    True: "Гарантийный случай", False: "Негарантийный случай", None: "Не классифицирована",
}

# Заявки, которые админ одобряет/отклоняет (регистрация, смена номера, сброс
# пароля, документы, добавление ШУ по фото)
REQUEST_STATUS: Labels = {
    "pending": ("На рассмотрении", "Ожидает"), "approved": "Одобрена", "rejected": "Отклонена",
    "cancelled": "Отменена",
}

SERVICE_REQUEST_STATUS: Labels = {
    "open": "Открыта", "in_progress": "В работе", "postponed": "Отложена", "closed": "Закрыта",
}
SERVICE_REQUEST_TYPE: Labels = {
    "repair": "Ремонт", "diagnostics": "Диагностика", "remote_adjustment": "Удалённая настройка",
    "onsite_adjustment": "Выездная настройка", "other": "Другое",
}

USER_TYPE: Labels = {
    "individual": ("Физическое лицо", "Физлицо"), "organization": ("Организация", "Юридическое лицо"),
}


def _starts_a_word(needle: str, label: str) -> bool:
    # С начала слова, а не из середины: "гарантийный" не должно находить
    # "Негарантийный случай"
    return any(word.startswith(needle) for word in label.split())


def label_codes(word: str, labels: Labels) -> list:
    """Коды, у чьей подписи какое-то слово начинается со слова запроса. Одна
    буква слишком шумная ("ш" подошла бы почти ко всем подписям) — короче двух
    символов не ищем."""
    needle = word.strip().lower()
    if len(needle) < 2:
        return []
    found = []
    for code, label in labels.items():
        variants = (label,) if isinstance(label, str) else label
        if any(_starts_a_word(needle, variant.lower()) for variant in variants):
            found.append(code)
    return found


def label_condition(word: str, column: ColumnElement, labels: Labels) -> ColumnElement | None:
    codes = label_codes(word, labels)
    return column.in_(codes) if codes else None


def warranty_label_condition(word: str, column: ColumnElement) -> ColumnElement | None:
    codes = label_codes(word, RECLAMATION_WARRANTY)
    if not codes:
        return None
    parts = [column.is_(None) if code is None else column.is_(code) for code in codes]
    return or_(*parts)
