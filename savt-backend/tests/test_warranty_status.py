"""warranty_status — чистая классификация гарантии по дате окончания.

Используется и для бейджа гарантии ШУ/проекта в списках, и как основа для
is_under_warranty у сервисных заявок (см. test_service_request_warranty.py) —
ошибка в границе 30 дней или в знаке сравнения напрямую бьёт по биллингу
(платно/бесплатно).
"""
from datetime import datetime, timedelta, timezone

from app.utils.warranty import warranty_status


def test_no_warranty_date_is_none():
    assert warranty_status(None) == "none"


def test_past_date_is_expired():
    assert warranty_status(datetime.now(timezone.utc) - timedelta(days=1)) == "expired"


def test_far_future_date_is_active():
    assert warranty_status(datetime.now(timezone.utc) + timedelta(days=365)) == "active"


def test_within_30_days_is_expiring_soon():
    assert warranty_status(datetime.now(timezone.utc) + timedelta(days=10)) == "expiring_soon"


def test_just_under_30_days_is_expiring_soon():
    assert warranty_status(datetime.now(timezone.utc) + timedelta(days=29)) == "expiring_soon"


def test_well_over_30_days_is_active():
    assert warranty_status(datetime.now(timezone.utc) + timedelta(days=31)) == "active"
