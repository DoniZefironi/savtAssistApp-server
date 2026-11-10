"""Ночная уборка файлов без ссылок: что удаляется, что остаётся, пробный режим, потолок за запуск и
отказ при сбое проверки ссылок. Файлы пишутся во временный каталог, время изменения подменяется."""
import logging
import os
import time

import pytest

from app.config import settings
from app.models.chat_user_settings import ChatUserSettings
from app.repositories.chat import MessageRepository
from app.services import file_references, orphan_files, upload_service


@pytest.fixture
def root(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    for folder in orphan_files.FOLDERS:
        (uploads / folder).mkdir(parents=True)
    monkeypatch.setattr(upload_service, "UPLOAD_ROOT", uploads)
    monkeypatch.setattr(settings, "orphan_files_mode", "delete")
    monkeypatch.setattr(settings, "orphan_files_min_age_days", 14)
    monkeypatch.setattr(settings, "orphan_files_max_per_run", 500)
    return uploads


def _file(root, folder, name, age_days, data=b"x"):
    path = root / folder / name
    path.write_bytes(data)
    moment = time.time() - age_days * 24 * 60 * 60
    os.utime(path, (moment, moment))
    return path


async def _message_with_file(db_session, user, url):
    from app.models.chat import Chat
    from app.models.message import Message

    chat = Chat(user_id=user.id, chat_type="notes")
    db_session.add(chat)
    await db_session.flush()
    message = Message(chat_id=chat.id, sender_id=user.id, text="Файл")
    db_session.add(message)
    await db_session.flush()
    await MessageRepository(db_session).add_attachment(message.id, {
        "attachment_type": "document", "file_url": url, "file_name": "f",
        "file_size_bytes": 1, "mime_type": "application/pdf",
    })


async def test_old_files_without_references_are_deleted_and_everything_else_is_kept(
    db_session, make_user, make_document, root,
):
    user = await make_user()
    orphans = [
        _file(root, "photos", "orphan.jpg", 30), _file(root, "files", "orphan.pdf", 20),
        _file(root, "voices", "orphan.ogg", 15, b"voice"),
    ]
    kept = {
        "young orphan": _file(root, "photos", "new.jpg", 1),
        "document": _file(root, "documents", "doc.pdf", 30),
        "message attachment": _file(root, "files", "in-message.pdf", 30),
        "wallpaper": _file(root, "photos", "wallpaper.jpg", 30),
    }
    (root / "photos" / "sub").mkdir()
    deep = _file(root / "photos", "sub", "deep.jpg", 30)             # вложенные папки не заходим
    (root / "elsewhere").mkdir()
    foreign = _file(root, "elsewhere", "x.bin", 30)                  # чужая папка не входит в загрузки
    await make_document(file_url="/static/documents/doc.pdf")
    await _message_with_file(db_session, user, "/static/files/in-message.pdf")
    db_session.add(ChatUserSettings(user_id=user.id, wallpaper_url="/static/photos/wallpaper.jpg"))
    await db_session.flush()

    stats = await orphan_files.run_cleanup(db_session)

    assert [p.exists() for p in orphans] == [False, False, False]
    assert all(p.exists() for p in kept.values()) and deep.exists() and foreign.exists()
    assert (stats["checked"], stats["orphans"], stats["deleted"]) == (6, 3, 3)
    assert stats["deleted_bytes"] == 1 + 1 + 5


@pytest.mark.parametrize("mode", ["dry_run", "something-unknown"])
async def test_dry_run_and_unknown_modes_delete_nothing_and_say_what_they_would_do(
    db_session, root, monkeypatch, caplog, mode,
):
    monkeypatch.setattr(settings, "orphan_files_mode", mode)
    orphan = _file(root, "photos", "orphan.jpg", 30)

    with caplog.at_level(logging.INFO, logger=orphan_files.logger.name):
        stats = await orphan_files.run_cleanup(db_session)

    assert orphan.exists() and stats["orphans"] == 1 and stats["deleted"] == 0
    assert "Ничего не удалено" in caplog.text and "/static/photos/orphan.jpg" in caplog.text


async def test_the_off_mode_does_nothing(db_session, root, monkeypatch):
    monkeypatch.setattr(settings, "orphan_files_mode", "off")
    orphan = _file(root, "photos", "orphan.jpg", 30)

    stats = await orphan_files.run_cleanup(db_session)

    assert orphan.exists() and stats["checked"] == 0


async def test_only_the_oldest_files_go_when_the_per_run_cap_is_reached(db_session, root, monkeypatch):
    monkeypatch.setattr(settings, "orphan_files_max_per_run", 2)
    files = [_file(root, "photos", f"f{age}.jpg", age) for age in (20, 40, 30, 25, 60)]

    stats = await orphan_files.run_cleanup(db_session)

    assert [p.exists() for p in files] == [True, False, True, True, False]     # ушли 60 и 40 суток
    assert stats["deleted"] == 2 and stats["limited"] is True


async def test_nothing_is_deleted_when_the_references_cannot_be_read(db_session, root, monkeypatch):
    orphan = _file(root, "photos", "orphan.jpg", 30)

    async def broken(session):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(file_references, "referenced_urls", broken)

    with pytest.raises(RuntimeError):
        await orphan_files.run_cleanup(db_session)

    assert orphan.exists()


async def test_a_reference_stored_with_a_signature_still_protects_the_file(db_session, make_document, root):
    protected = _file(root, "documents", "signed.pdf", 30)
    await make_document(file_url="/static/documents/signed.pdf?md5=abc&expires=1")

    stats = await orphan_files.run_cleanup(db_session)

    assert protected.exists() and stats["orphans"] == 0


async def test_the_nightly_job_skips_the_run_when_the_mode_is_off(monkeypatch):
    from app import main

    called = []

    async def run_cleanup(session):
        called.append(1)

    monkeypatch.setattr(orphan_files, "run_cleanup", run_cleanup)
    monkeypatch.setattr(settings, "orphan_files_mode", "off")
    await main._orphan_files_job()
    assert called == []
