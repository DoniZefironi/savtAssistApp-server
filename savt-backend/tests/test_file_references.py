"""Файл на диске не стирается вместе с чатом, пока на него ссылается что-то ещё: клиент может
приложить к своему сообщению ссылку на документ или фото, и удаление его чата не должно
уносить файл у остальных. Файлы пишутся во временный каталог."""
import pytest

from app.database import Base
from app.repositories.chat import MessageRepository
from app.services import chat_service, file_references, upload_service


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def storage(tmp_path, monkeypatch, db_session):
    root = tmp_path / "uploads"
    for folder in ("documents", "files"):
        (root / folder).mkdir(parents=True)
    monkeypatch.setattr(upload_service, "UPLOAD_ROOT", root)
    pending = []
    monkeypatch.setattr(chat_service, "spawn", pending.append)
    monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: _SessionContext(db_session))
    return root, pending


async def _run_cleanup(pending):
    for task in pending:
        await task
    pending.clear()


async def _message_with_files(db_session, chat, user, *urls):
    from app.models.message import Message

    message = Message(chat_id=chat.id, sender_id=user.id, text="Вложения")
    db_session.add(message)
    await db_session.flush()
    for url in urls:
        await MessageRepository(db_session).add_attachment(message.id, {
            "attachment_type": "document", "file_url": url, "file_name": "f",
            "file_size_bytes": 1, "mime_type": "application/pdf",
        })
    return message


def test_every_column_with_a_file_url_is_registered():
    """Новая колонка со ссылкой на файл должна попасть в file_url_columns, иначе файл,
    которым ещё пользуются, могут стереть вместе с чужим чатом."""
    import app.models  # noqa: F401  — регистрирует все таблицы

    registered = {(c.table.name, c.name) for c in file_references.file_url_columns()}
    url_columns = {
        (table.name, column.name)
        for table in Base.metadata.sorted_tables for column in table.columns if column.name.endswith("url")
    }

    assert url_columns == registered


async def test_only_unused_urls_are_reported(db_session, make_user, make_chat, make_document):
    user = await make_user()
    chat = await make_chat(user, "notes")
    await make_document(file_url="/static/documents/shared.pdf")
    await _message_with_files(db_session, chat, user, "/static/files/in-message.jpg")

    result = await file_references.unreferenced_urls(db_session, [
        "/static/documents/shared.pdf", "/static/files/in-message.jpg",
        "/static/files/orphan.jpg", "/static/files/orphan.jpg", "",
    ])

    assert result == ["/static/files/orphan.jpg"]
    assert await file_references.unreferenced_urls(db_session, []) == []


async def test_deleting_a_chat_keeps_a_file_that_a_document_still_uses(
    db_session, make_user, make_chat, make_document, storage,
):
    root, pending = storage
    (root / "documents" / "manual.pdf").write_bytes(b"manual")
    (root / "files" / "private.jpg").write_bytes(b"photo")
    user = await make_user()
    chat = await make_chat(user, "notes")
    await make_document(file_url="/static/documents/manual.pdf")
    await _message_with_files(
        db_session, chat, user, "/static/documents/manual.pdf", "/static/files/private.jpg",
    )

    await chat_service.ChatService(db_session).delete_chat(chat.id, user.id)
    await _run_cleanup(pending)

    assert (root / "documents" / "manual.pdf").exists()          # общий документ остался
    assert not (root / "files" / "private.jpg").exists()          # файл только из этого чата стёрт


async def test_nothing_is_deleted_when_the_check_fails(db_session, make_user, make_chat, storage, monkeypatch):
    root, pending = storage
    (root / "files" / "private.jpg").write_bytes(b"photo")
    user = await make_user()
    chat = await make_chat(user, "notes")
    await _message_with_files(db_session, chat, user, "/static/files/private.jpg")

    async def broken(session, urls):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(file_references, "unreferenced_urls", broken)

    await chat_service.ChatService(db_session).delete_chat(chat.id, user.id)
    await _run_cleanup(pending)

    assert (root / "files" / "private.jpg").exists()
