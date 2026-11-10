"""Суточная квота на загрузки обычного пользователя: резерв места до сохранения файла, окно в
сутки, освобождение при неудаче, сотрудники без квоты. Файлы пишутся во временный каталог."""
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.config import settings
from app.core.security import create_access_token
from app.models.upload_log import UploadLog
from app.routers import upload as upload_router
from app.services import upload_quota, upload_service

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(upload_service, "UPLOAD_ROOT", root)
    monkeypatch.setattr(upload_router, "UPLOAD_ROOT", root)
    monkeypatch.setattr(settings, "user_upload_quota_mb_per_day", 1)
    return root


def _auth(user, role="user"):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


async def _logged(db_session, user_id):
    rows = (await db_session.execute(
        select(UploadLog.size_bytes).where(UploadLog.user_id == user_id).order_by(UploadLog.id)
    )).scalars().all()
    return list(rows)


# --- сам учёт ---

async def test_a_reservation_is_recorded_and_the_next_one_that_does_not_fit_is_refused(db_session, make_user):
    user = await make_user()
    user_id = user.id                     # после отказа сессия откатывается, и объект пользователя протухает

    first = await upload_quota.reserve(db_session, user_id, 600_000)
    with pytest.raises(HTTPException) as refused:
        await upload_quota.reserve(db_session, user_id, 600_000)

    assert first is not None
    assert refused.value.status_code == 429 and "1 МБ" in refused.value.detail
    assert await _logged(db_session, user_id) == [600_000]        # отказ ничего не записал


async def test_finalize_replaces_the_declared_size_and_release_gives_the_place_back(db_session, make_user):
    user = await make_user()
    kept = await upload_quota.reserve(db_session, user.id, 900_000)
    dropped = await upload_quota.reserve(db_session, user.id, 50_000)

    await upload_quota.finalize(db_session, kept, 10)
    await upload_quota.release(db_session, dropped)

    assert await _logged(db_session, user.id) == [10]
    assert await upload_quota.reserve(db_session, user.id, 1_000_000) is not None     # места снова достаточно


async def test_only_the_last_twenty_four_hours_count(db_session, make_user):
    user = await make_user()
    db_session.add(UploadLog(
        user_id=user.id, size_bytes=5 * MB, created_at=datetime.now(timezone.utc) - timedelta(hours=25),
    ))
    await db_session.flush()

    assert await upload_quota.reserve(db_session, user.id, 500_000) is not None


async def test_quota_of_one_user_does_not_affect_another(db_session, make_user):
    first, second = await make_user(), await make_user()
    await upload_quota.reserve(db_session, first.id, 900_000)

    assert await upload_quota.reserve(db_session, second.id, 900_000) is not None


async def test_a_zero_quota_turns_the_check_off(db_session, make_user, monkeypatch):
    monkeypatch.setattr(settings, "user_upload_quota_mb_per_day", 0)
    user = await make_user()

    assert await upload_quota.reserve(db_session, user.id, 500 * MB) is None
    assert await _logged(db_session, user.id) == []


async def test_old_log_rows_are_pruned(db_session, make_user):
    user = await make_user()
    db_session.add_all([
        UploadLog(user_id=user.id, size_bytes=1, created_at=datetime.now(timezone.utc) - timedelta(days=31)),
        UploadLog(user_id=user.id, size_bytes=2, created_at=datetime.now(timezone.utc) - timedelta(days=2)),
    ])
    await db_session.flush()

    removed = await upload_quota.prune_old(db_session)

    assert removed == 1 and await _logged(db_session, user.id) == [2]


# --- через HTTP ---

def _pdf(size):
    return {"file": ("a.pdf", io.BytesIO(b"x" * size), "application/pdf")}


async def test_a_user_over_the_quota_is_refused_and_nothing_is_saved(api, db_session, make_user, storage):
    user = await make_user()
    user_id = user.id

    first = await api.post("/upload/attachment", headers=_auth(user), files=_pdf(600_000))
    second = await api.post("/upload/attachment", headers=_auth(user), files=_pdf(600_000))

    assert first.status_code == 200
    assert second.status_code == 429 and "Суточный лимит загрузок исчерпан" in second.json()["detail"]
    assert len(list(storage.rglob("*.pdf"))) == 1                  # второй файл на диск не попал
    assert await _logged(db_session, user_id) == [600_000]         # записан фактический размер


async def test_voice_messages_count_against_the_same_quota(api, make_user):
    user = await make_user()
    voice = lambda size: {"file": ("v.ogg", io.BytesIO(b"v" * size), "audio/ogg")}   # noqa: E731

    first = await api.post("/upload/voice", headers=_auth(user), files=voice(600_000))
    second = await api.post("/upload/attachment", headers=_auth(user), files=_pdf(600_000))

    assert first.status_code == 200 and second.status_code == 429


async def test_staff_are_not_limited(api, make_user, db_session):
    operator = await make_user("operator")

    responses = [
        await api.post("/upload/attachment", headers=_auth(operator, "operator"), files=_pdf(600_000))
        for _ in range(3)
    ]

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert await _logged(db_session, operator.id) == []


async def test_a_rejected_upload_does_not_spend_the_quota(api, make_user, db_session):
    user = await make_user()

    rejected = await api.post(
        "/upload/voice", headers=_auth(user), files={"file": ("v.txt", io.BytesIO(b"v" * 600_000), "text/plain")},
    )
    accepted = await api.post("/upload/attachment", headers=_auth(user), files=_pdf(600_000))

    assert rejected.status_code == 415 and accepted.status_code == 200
    assert await _logged(db_session, user.id) == [600_000]


async def test_an_oversized_file_gives_the_place_back(api, make_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "user_upload_max_mb", 1)        # файл в 1,2 МБ больше лимита одного файла
    monkeypatch.setattr(settings, "user_upload_quota_mb_per_day", 10)
    user = await make_user()
    user_id = user.id

    response = await api.post("/upload/attachment", headers=_auth(user), files=_pdf(1_200_000))

    assert response.status_code == 413
    assert await _logged(db_session, user_id) == []


async def test_the_reservation_count_matches_the_database(db_session, make_user):
    user = await make_user()
    await upload_quota.reserve(db_session, user.id, 100)
    await upload_quota.reserve(db_session, user.id, 200)

    count = (await db_session.execute(select(func.count()).select_from(UploadLog))).scalar()

    assert count >= 2 and await _logged(db_session, user.id) == [100, 200]
