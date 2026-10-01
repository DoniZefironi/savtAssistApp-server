"""Правила обязательных полей при смене статуса рекламации (_check_transition).

Чистая логика, без БД и без Bitrix — эти правила уже не раз ломались/менялись
за время разработки (обязательность гарантии то вводили, то снимали; документ
при закрытии не требовался для rejected/invalid, пока не выяснилось, что Bitrix
требует его на всех трёх закрывающих стадиях), поэтому именно здесь
регрессионный тест окупается быстрее всего.
"""
from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.services.reclamation_service import ReclamationService


def _rec(**attrs) -> SimpleNamespace:
    """Минимальный объект с теми же атрибутами, что читает _check_transition —
    настоящая модель Reclamation тут не нужна, функция ничего не пишет в БД."""
    defaults = dict(
        rejection_reason=None, resolution_comment=None,
        responsible_name=None, confirmation_file_url=None,
    )
    defaults.update(attrs)
    return SimpleNamespace(**defaults)


def _check(rec, **changed):
    ReclamationService._check_transition(rec, changed)


# --- review: ничего не требуется ---

def test_review_requires_nothing():
    _check(_rec(), status="review")  # не бросает


# --- in_progress: нужен ответственный ---

def test_in_progress_without_responsible_name_rejected():
    with pytest.raises(ValidationError, match="ответственного"):
        _check(_rec(), status="in_progress")


def test_in_progress_with_responsible_name_in_patch_ok():
    _check(_rec(), status="in_progress", responsible_name="Петров Пётр")


def test_in_progress_with_responsible_name_already_on_record_ok():
    # ответственного назначили раньше, этим PATCH меняется что-то другое
    _check(_rec(responsible_name="Петров Пётр"), status="in_progress")


def test_in_progress_does_not_require_warranty_classification():
    # снято 2026-09-25 по прямому решению — не наше дело Bitrix для этой стадии
    _check(_rec(responsible_name="Петров Пётр"), status="in_progress")


# --- resolved: комментарий + документ (+ ответственный уже должен быть) ---

def test_resolved_without_resolution_comment_rejected():
    with pytest.raises(ValidationError, match="итогового комментария"):
        _check(_rec(), status="resolved")


def test_resolved_with_comment_but_without_document_rejected():
    with pytest.raises(ValidationError, match="подтверждающего документа"):
        _check(_rec(resolution_comment="Заменили датчик"), status="resolved")


def test_resolved_fully_filled_ok():
    _check(
        _rec(resolution_comment="Заменили датчик", confirmation_file_url="https://x/doc.pdf"),
        status="resolved",
    )


# --- rejected / invalid: причина + документ (комментарий не нужен) ---

@pytest.mark.parametrize("status", ["rejected", "invalid"])
def test_rejecting_without_reason_rejected(status):
    with pytest.raises(ValidationError, match="без указания причины"):
        _check(_rec(), status=status)


@pytest.mark.parametrize("status", ["rejected", "invalid"])
def test_rejecting_with_reason_but_without_document_rejected(status):
    # частая ошибка: документ обязателен и тут тоже, не только при resolved
    with pytest.raises(ValidationError, match="подтверждающего документа"):
        _check(_rec(rejection_reason="Гарантия истекла"), status=status)


@pytest.mark.parametrize("status", ["rejected", "invalid"])
def test_rejecting_fully_filled_ok(status):
    _check(
        _rec(rejection_reason="Гарантия истекла", confirmation_file_url="https://x/doc.pdf"),
        status=status,
    )


# --- порядок проверок: причина отклонения проверяется раньше документа ---

def test_rejected_without_reason_and_without_document_reports_reason_first():
    rec = _rec()  # ни причины, ни документа
    with pytest.raises(ValidationError, match="без указания причины"):
        _check(rec, status="rejected")
