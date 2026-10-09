"""Папки проектов на NAS: раскладка по годам, шаблон подпапок и подпапки ШУ,
переименование и переезд, зеркалирование документов, выгрузка фото и переписки,
обратная подхватка файлов, ночной проход и фоновые обёртки. NAS и каталог загрузок —
временные каталоги, Bitrix не используется."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.cabinet_photo import CabinetPhoto
from app.models.document import Document
from app.models.message import Message
from app.models.message_attchment import MessageAttachment
from app.repositories.project import ProjectRepository
from app.services import project_folder_service as pfs
from app.services import upload_service

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001"
    "e221bc330000000049454e44ae426082"
)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def nas(tmp_path, monkeypatch, db_session):
    root, uploads = tmp_path / "nas", tmp_path / "uploads"
    root.mkdir()
    for folder in ("documents", "photos", "files"):
        (uploads / folder).mkdir(parents=True)
    monkeypatch.setattr(settings, "project_folders_root", str(root))
    monkeypatch.setattr(settings, "public_base_url", "https://app.test")
    monkeypatch.setattr(pfs, "UPLOAD_ROOT", uploads)
    monkeypatch.setattr(upload_service, "UPLOAD_ROOT", uploads)
    monkeypatch.setattr(pfs, "AsyncSessionLocal", lambda: _SessionContext(db_session))
    pending = []
    monkeypatch.setattr(pfs, "spawn", pending.append)
    return SimpleNamespace(root=root, uploads=uploads, pending=pending)


async def _run_background(nas):
    for task in nas.pending:
        await task
    nas.pending.clear()


def _upload(nas, folder, name, data=b"data"):
    path = nas.uploads / folder / name
    path.write_bytes(data)
    return f"/static/{folder}/{name}"


# --- чистые функции ---

def test_names_are_made_safe_for_the_filesystem():
    assert pfs.sanitize_folder_name('Насосная: "№1"/зал?') == "Насосная_ _№1__зал_"
    assert pfs.sanitize_folder_name('a\\b/c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert pfs.sanitize_folder_name("  Космос...  ") == "Космос"
    assert pfs.sanitize_folder_name("") == "Без_названия" and pfs.sanitize_folder_name("...") == "Без_названия"
    assert len(pfs.sanitize_folder_name("а" * 400)) == 150
    assert pfs.strip_year_prefix("26_052 ЖК Сосны") == "052 ЖК Сосны" and pfs.strip_year_prefix("Без номера") == "Без номера"
    assert pfs._mirrored_filename("План/схема", "/static/documents/x.pdf") == "План_схема.pdf"
    assert pfs._mirrored_filename("Без файла", None) == "Без файла"


def test_cabinet_folder_name():
    assert pfs._cabinet_folder_name(SimpleNamespace(id=7, object_number="29_099", admin_internal_name="ШУ-18К")) == "29_099 ШУ-18К"
    assert pfs._cabinet_folder_name(SimpleNamespace(id=7, object_number="29_099", admin_internal_name=None)) == "29_099"
    assert pfs._cabinet_folder_name(SimpleNamespace(id=7, object_number=None, admin_internal_name=None)) == "ШУ-7"


def test_sync_eligibility_follows_the_latest_warranty():
    now = datetime.now(timezone.utc)
    cabinet = lambda days: SimpleNamespace(warranty_ends_at=now + timedelta(days=days))   # noqa: E731

    assert pfs.is_sync_eligible([]) is True                                            # ничего не заполнено — синхронизируем
    assert pfs.is_sync_eligible([cabinet(-30), cabinet(10)]) is True                    # решает самая поздняя
    assert pfs.is_sync_eligible([cabinet(-30)]) is False
    assert pfs.is_sync_eligible([cabinet(-3)]) is True                                  # запас на неделю после окончания
    assert pfs.is_sync_eligible([cabinet(100)], SimpleNamespace(warranty_ends_at=now - timedelta(days=60))) is False
    assert pfs.is_sync_eligible([cabinet(-100)], SimpleNamespace(warranty_ends_at=now + timedelta(days=60))) is True


async def test_paths_follow_the_year_and_the_parent_chain(db_session, nas, make_project):
    parent = await make_project(name="26_100 Родитель", production_number="26_100", folder_name="Родитель")
    child = await make_project(name="25_200 Дочерний", production_number="25_200", parent_project_id=parent.id)
    repo = ProjectRepository(db_session)

    assert pfs._year_folder_name(parent) == "!2026"
    assert await pfs._parent_root_path(parent, repo) == nas.root / "!2026"
    # год ветки определяет корень, номер ребёнка (25) значения не имеет
    assert await pfs._project_root_path(child, repo) == nas.root / "!2026" / "Родитель" / "200 Дочерний"
    assert await pfs._legacy_parent_root_path(child, repo) == nas.root / "Родитель"


# --- создание структуры ---

async def test_creating_a_project_folder_lays_out_the_template_and_qr(db_session, nas, make_project):
    project = await make_project(name="26_100 Космос", production_number="26_100", unique_code="code-1")

    await pfs.create_project_folder_structure(project, ProjectRepository(db_session))

    root = nas.root / "!2026" / "100 Космос"
    for sub in pfs.TEMPLATE_SUBFOLDERS:
        assert (root / sub).is_dir()
    assert (root / "_Маркировка" / "QR.png").read_bytes().startswith(b"\x89PNG")


async def test_nothing_is_created_without_a_configured_root(db_session, nas, make_project, monkeypatch):
    monkeypatch.setattr(settings, "project_folders_root", "")
    project = await make_project()

    await pfs.create_project_folder_structure(project, ProjectRepository(db_session))
    await pfs.sync_project_folder(db_session, project)
    await pfs.relocate_project_folder(db_session, project)
    assert await pfs.import_new_files_from_nas(db_session, nas.root, [], project_id=project.id) == 0
    await pfs.sync_all_project_folders()

    assert list(nas.root.iterdir()) == []


async def test_cabinet_subfolders_go_into_each_category(nas, make_cabinet):
    cabinet = SimpleNamespace(id=1, object_number="29_099", admin_internal_name="Насосная")

    await pfs._ensure_cabinet_structure(nas.root, cabinet)

    for category in pfs.CABINET_CATEGORIES:
        assert (nas.root / category / "29_099 Насосная").is_dir()
    for subtype in pfs._MARKING_SUBTYPES:
        assert (nas.root / "_Маркировка" / "29_099 Насосная" / subtype).is_dir()


# --- зеркало документов ---

async def test_documents_are_mirrored_and_removed(db_session, nas, make_document):
    doc = await make_document(title="Паспорт", file_url=_upload(nas, "documents", "p.pdf", b"%PDF"))
    ghost = await make_document(title="Нет файла", file_url="/static/documents/gone.pdf")
    target = nas.root / "guide"
    target.mkdir()

    await pfs.mirror_document_to_nas(target, doc)
    await pfs.mirror_document_to_nas(target, ghost)
    await pfs.mirror_document_to_nas(target, SimpleNamespace(file_url=None, title="x"))
    assert [p.name for p in target.iterdir()] == ["Паспорт.pdf"]

    await pfs.remove_document_from_nas(target, "Паспорт", "/static/documents/p.pdf")
    await pfs.remove_document_from_nas(target, "Паспорт", "/static/documents/p.pdf")   # повторно — не ошибка
    assert list(target.iterdir()) == []


# --- переименование и переезд ---

async def test_renamed_project_folder_is_moved(db_session, nas, make_project):
    project = await make_project(name="26_100 Новое имя", production_number="26_100", folder_name="Старое имя")
    old = nas.root / "!2026" / "Старое имя"
    (old / "Фото").mkdir(parents=True)
    (old / "Фото" / "a.png").write_bytes(PNG)

    await pfs.sync_project_folder(db_session, project)

    new = nas.root / "!2026" / "100 Новое имя"
    assert not old.exists() and (new / "Фото" / "a.png").exists()    # файлы переехали вместе с папкой
    assert project.folder_name == "100 Новое имя"


async def test_flat_legacy_folder_moves_into_its_year(db_session, nas, make_project):
    project = await make_project(name="26_100 Космос", production_number="26_100", folder_name="100 Космос")
    legacy = nas.root / "100 Космос"
    (legacy / "_Проект").mkdir(parents=True)
    (legacy / "_Проект" / "scheme.dwg").write_bytes(b"dwg")

    await pfs.sync_project_folder(db_session, project)

    assert not legacy.exists() and (nas.root / "!2026" / "100 Космос" / "_Проект" / "scheme.dwg").exists()


async def test_relocation_is_refused_when_two_projects_share_a_folder_name(db_session, nas, make_project, caplog):
    first = await make_project(name="26_100 Дубль", production_number="26_100", folder_name="100 Дубль")
    await make_project(name="26_100 Дубль", production_number="26_100", folder_name="100 Дубль")
    legacy = nas.root / "100 Дубль"
    legacy.mkdir()

    await pfs.relocate_project_folder(db_session, first)

    assert legacy.exists() and not (nas.root / "!2026" / "100 Дубль").exists()


async def test_expired_projects_only_move_and_never_get_empty_folders(db_session, nas, make_project):
    moves = await make_project(name="26_100 Архив", production_number="26_100", folder_name="100 Архив")
    never_had_a_folder = await make_project(name="26_101 Без папки", production_number="26_101")
    (nas.root / "100 Архив").mkdir()

    await pfs.relocate_project_folder(db_session, moves)
    await pfs.relocate_project_folder(db_session, never_had_a_folder)

    assert (nas.root / "!2026" / "100 Архив").is_dir() and moves.folder_name == "100 Архив"
    assert not (nas.root / "!2026" / "101 Без папки").exists() and never_had_a_folder.folder_name is None


async def test_renamed_cabinet_subfolders_follow_the_new_name(db_session, nas, make_project, make_cabinet):
    project = await make_project(name="26_100 Космос", production_number="26_100")
    cabinet = await make_cabinet(project_id=project.id, object_number="29_001", admin_internal_name="Старое",
                                 folder_name="29_001 Старое")
    root = nas.root / "!2026" / "100 Космос"
    (root / "Фото" / "29_001 Старое").mkdir(parents=True)
    (root / "Фото" / "29_001 Старое" / "a.png").write_bytes(PNG)
    cabinet.admin_internal_name = "Новое"

    await pfs._relocate_cabinet_structure(root, cabinet, db_session)

    assert (root / "Фото" / "29_001 Новое" / "a.png").exists() and not (root / "Фото" / "29_001 Старое").exists()
    assert cabinet.folder_name == "29_001 Новое"


# --- фото ---

async def _photo(db_session, nas, **kw):
    photo = CabinetPhoto(url=kw.pop("url", None) or _upload(nas, "photos", f"{len(list(nas.uploads.glob('photos/*')))}.png", PNG), **kw)
    db_session.add(photo)
    await db_session.flush()
    return photo


async def test_photos_are_copied_once_with_ordered_names(db_session, nas, make_cabinet):
    cabinet = await make_cabinet()
    first = await _photo(db_session, nas, cabinet_id=cabinet.id, caption="Вид спереди", sort_order=1)
    second = await _photo(db_session, nas, cabinet_id=cabinet.id, sort_order=2)
    missing = await _photo(db_session, nas, cabinet_id=cabinet.id, url="/static/photos/gone.png", sort_order=3)
    dest = nas.root / "Фото"

    remaining = await pfs.export_photos(db_session, dest, cabinet_id=cabinet.id)
    await pfs.export_photos(db_session, dest, cabinet_id=cabinet.id)

    assert sorted(p.name for p in dest.iterdir()) == ["001 Вид спереди.png", "002 Фото 2.png"]
    assert (first.nas_filename, second.nas_filename, missing.nas_filename) == ("001 Вид спереди.png", "002 Фото 2.png", None)
    assert len(remaining) == 3 and first.nas_mtime is not None


async def test_photo_deleted_on_the_nas_disappears_in_the_app_only_when_the_folder_was_there(db_session, nas, make_cabinet):
    cabinet = await make_cabinet()
    photo = await _photo(db_session, nas, cabinet_id=cabinet.id, nas_filename="001 x.png", nas_mtime=1.0)
    photo_id = photo.id
    dest = nas.root / "Фото"

    await pfs.export_photos(db_session, dest, cabinet_id=cabinet.id)          # папки не было — похоже на сбой, фото цело
    assert await db_session.get(CabinetPhoto, photo_id) is not None

    remaining = await pfs.export_photos(db_session, dest, cabinet_id=cabinet.id)   # папка уже на месте — удалили вручную
    assert remaining == [] and await db_session.get(CabinetPhoto, photo_id) is None


async def test_replaced_photo_content_is_reloaded(db_session, nas, make_cabinet):
    cabinet = await make_cabinet()
    dest = nas.root / "Фото"
    dest.mkdir()
    file = dest / "001 x.png"
    file.write_bytes(PNG)
    old_url = "/static/photos/old.png"
    photo = await _photo(db_session, nas, cabinet_id=cabinet.id, url=old_url, nas_filename="001 x.png",
                         nas_mtime=file.stat().st_mtime - 100)

    await pfs.export_photos(db_session, dest, cabinet_id=cabinet.id)

    assert photo.url != old_url and photo.url.startswith("/static/photos/")
    assert photo.nas_mtime == pytest.approx(file.stat().st_mtime)


async def test_first_check_only_records_the_baseline_and_non_images_are_ignored(db_session, nas, make_cabinet):
    cabinet = await make_cabinet()
    dest = nas.root / "Фото"
    dest.mkdir()
    (dest / "001 a.png").write_bytes(PNG)
    (dest / "002 b.txt").write_text("не картинка")
    first = await _photo(db_session, nas, cabinet_id=cabinet.id, url="/static/photos/a.png", nas_filename="001 a.png", sort_order=1)
    text = await _photo(db_session, nas, cabinet_id=cabinet.id, url="/static/photos/b.png", nas_filename="002 b.txt",
                        nas_mtime=1.0, sort_order=2)

    await pfs.export_photos(db_session, dest, cabinet_id=cabinet.id)

    assert first.nas_mtime is not None and first.url == "/static/photos/a.png"
    assert text.url == "/static/photos/b.png"       # файл подменили не картинкой — запись не трогаем


async def test_photos_put_on_the_nas_are_picked_up_but_other_files_are_not(db_session, nas, make_project):
    project = await make_project()
    dest = nas.root / "Фото"
    dest.mkdir()
    (dest / "объект.png").write_bytes(PNG)
    (dest / "скан.pdf").write_bytes(b"%PDF")
    (dest / "~$temp.png").write_bytes(PNG)
    (dest / "подпапка").mkdir()
    known = [SimpleNamespace(nas_filename="известное.png", sort_order=4)]

    imported = await pfs.import_new_photos_from_nas(db_session, dest, known, project_id=project.id)
    again = await pfs.import_new_photos_from_nas(
        db_session, dest, [SimpleNamespace(nas_filename="объект.png", sort_order=5)], project_id=project.id,
    )

    [photo] = (await db_session.execute(select(CabinetPhoto).where(CabinetPhoto.project_id == project.id))).scalars().all()
    assert imported == 1 and again == 0
    assert (photo.caption, photo.nas_filename, photo.sort_order) == ("объект", "объект.png", 5)
    assert await pfs.import_new_photos_from_nas(db_session, nas.root / "нет такой", [], project_id=project.id) == 0


# --- переписка ---

async def _chat_with_messages(db_session, nas, make_user, make_chat, **chat_kw):
    owner = await make_user(full_name="Иванов Иван")
    operator = await make_user("operator", full_name="Оператор Олег")
    chat = await make_chat(owner, **chat_kw)
    first = Message(chat_id=chat.id, sender_id=owner.id, text="Не включается насос")
    gone = Message(chat_id=chat.id, sender_id=operator.id, text="секрет", deleted_at=datetime.now(timezone.utc))
    reply = Message(chat_id=chat.id, sender_id=operator.id, text="Приедем завтра")
    db_session.add_all([first, gone, reply])
    await db_session.flush()
    stored = _upload(nas, "photos", "evidence.png", PNG)
    db_session.add_all([
        MessageAttachment(message_id=first.id, attachment_type="image", file_url=stored, file_name="Фото насоса.png",
                          file_size_bytes=len(PNG), mime_type="image/png"),
        MessageAttachment(message_id=reply.id, attachment_type="location", latitude=53.9, longitude=27.5),
        MessageAttachment(message_id=reply.id, attachment_type="file", file_url="/static/files/gone.pdf", file_name="нет.pdf",
                          file_size_bytes=1, mime_type="application/pdf"),
    ])
    await db_session.flush()
    return owner, chat


async def test_chat_transcript_and_attachments_are_exported(db_session, nas, make_user, make_chat, make_project):
    project = await make_project()
    _, chat = await _chat_with_messages(db_session, nas, make_user, make_chat, chat_type="project", project_id=project.id)
    dest = nas.root / "Переписка"
    dest.mkdir()

    exported = await pfs.export_chats(db_session, dest, project_id=project.id)

    transcript = (dest / "Чат проекта — Иванов Иван.txt").read_text(encoding="utf-8")
    assert exported == 1 and "Иванов Иван: Не включается насос" in transcript
    assert "(сообщение удалено)" in transcript and "секрет" not in transcript
    assert "[вложение: Фото насоса.png]" in transcript and "[геолокация: 53.9, 27.5]" in transcript
    first_id = (await db_session.execute(select(Message.id).where(Message.chat_id == chat.id).order_by(Message.id))).scalars().first()
    assert (dest / "вложения" / f"{first_id}_Фото насоса.png").read_bytes() == PNG
    assert len(list((dest / "вложения").iterdir())) == 1          # вложения без файла на диске пропущены


async def test_unchanged_chats_are_not_read_again_and_one_chat_can_be_exported(db_session, nas, make_user, make_chat, make_project):
    project = await make_project()
    _, chat = await _chat_with_messages(db_session, nas, make_user, make_chat, chat_type="project", project_id=project.id)
    chat.last_message_at = datetime.now(timezone.utc) - timedelta(days=1)
    dest = nas.root / "Переписка"
    dest.mkdir()
    await pfs.export_chats(db_session, dest, project_id=project.id)
    file = dest / "Чат проекта — Иванов Иван.txt"
    file.write_text("старое содержимое", encoding="utf-8")
    since = datetime.now(timezone.utc)

    skipped = await pfs.export_chats(db_session, dest, project_id=project.id, since=since)
    assert skipped == 0 and file.read_text(encoding="utf-8") == "старое содержимое"

    file.unlink()
    rebuilt = await pfs.export_chats(db_session, dest, project_id=project.id, since=since)    # файла нет — собираем заново
    assert rebuilt == 1 and file.exists()
    assert await pfs.export_chats(db_session, dest, project_id=project.id, only_chat_id=chat.id) == 1
    assert await pfs.export_chats(db_session, dest, project_id=project.id, only_chat_id=999999) == 0


async def test_request_chats_are_titled_by_request(db_session, nas, make_user, make_chat, make_cabinet):
    from app.models.service_request import ServiceRequest
    user = await make_user(full_name="Заявитель")
    cabinet = await make_cabinet()
    sr = ServiceRequest(user_id=user.id, cabinet_id=cabinet.id, request_type="repair", is_under_warranty=False,
                        description="Течёт", status="open")
    db_session.add(sr)
    await db_session.flush()
    chat = await make_chat(user, "service_request", cabinet_id=cabinet.id, service_request_id=sr.id)
    db_session.add(Message(chat_id=chat.id, sender_id=user.id, text="Помогите"))
    await db_session.flush()
    dest = nas.root / "Переписка"

    await pfs.export_chats(db_session, dest, cabinet_id=cabinet.id)

    assert (dest / f"Заявка {sr.id} — Заявитель.txt").exists()
    assert await pfs.export_chats(db_session, dest, cabinet_id=987654) == 0     # у шкафа нет чатов


# --- файлы, положенные прямо на NAS ---

async def test_files_dropped_into_the_guide_folder_become_internal_documents(db_session, nas, make_project, make_document):
    project = await make_project()
    known = await make_document(project_id=project.id, title="Паспорт", file_url="/static/documents/p.pdf")
    guide = nas.root / "_Руководство"
    guide.mkdir()
    (guide / "Паспорт.pdf").write_bytes(b"%PDF")          # зеркало известного документа
    (guide / "Договор.pdf").write_bytes(b"%PDF-new")
    (guide / "~$Договор.pdf").write_bytes(b"lock")
    (guide / "Вложенная").mkdir()

    imported = await pfs.import_new_files_from_nas(db_session, guide, [known], project_id=project.id)
    docs = (await db_session.execute(select(Document).where(Document.title == "Договор"))).scalars().all()
    again = await pfs.import_new_files_from_nas(db_session, guide, [known] + docs, project_id=project.id)

    assert imported == 1 and again == 0 and len(docs) == 1
    assert docs[0].is_internal is True and docs[0].requires_approval is False
    assert docs[0].nas_filename == "Договор.pdf" and docs[0].doc_type == "pdf"
    assert await pfs.import_new_files_from_nas(db_session, nas.root / "нет", [], project_id=project.id) == 0


# --- полная сверка ---

async def test_full_sync_lays_out_everything_and_is_repeatable(db_session, nas, make_user, make_chat, make_project, make_cabinet, make_document):
    project = await make_project(name="26_100 Космос", production_number="26_100")
    cabinet = await make_cabinet(project_id=project.id, object_number="29_001", admin_internal_name="Насосная")
    project_doc = await make_document(project_id=project.id, title="Паспорт проекта", file_url=_upload(nas, "documents", "pp.pdf", b"%PDF-p"))
    cabinet_doc = await make_document(cabinet_id=cabinet.id, title="Паспорт ШУ", file_url=_upload(nas, "documents", "pc.pdf", b"%PDF-c"))
    await _photo(db_session, nas, project_id=project.id, caption="Общий вид")
    await _photo(db_session, nas, cabinet_id=cabinet.id, caption="Шкаф")
    owner = await make_user(full_name="Клиент")
    project_chat = await make_chat(owner, "project", project_id=project.id)
    cabinet_chat = await make_chat(owner, "cabinet", cabinet_id=cabinet.id)
    db_session.add_all([Message(chat_id=project_chat.id, sender_id=owner.id, text="Вопрос по проекту"),
                        Message(chat_id=cabinet_chat.id, sender_id=owner.id, text="Вопрос по шкафу")])
    await db_session.flush()
    root = nas.root / "!2026" / "100 Космос"

    await pfs.sync_project_folder(db_session, project)
    (root / "_Руководство" / "Подсунутый файл.pdf").write_bytes(b"%PDF-x")
    await pfs.sync_project_folder(db_session, project)

    assert (root / "_Руководство" / "Паспорт проекта.pdf").read_bytes() == b"%PDF-p"
    assert (root / "_Руководство" / "29_001 Насосная" / "Паспорт ШУ.pdf").read_bytes() == b"%PDF-c"
    assert (root / "Фото" / "001 Общий вид.png").exists() and (root / "Фото" / "29_001 Насосная" / "001 Шкаф.png").exists()
    assert "Вопрос по проекту" in (root / "Переписка" / "Чат проекта — Клиент.txt").read_text(encoding="utf-8")
    assert "Вопрос по шкафу" in (root / "Переписка" / "29_001 Насосная" / "Чат ШУ — Клиент.txt").read_text(encoding="utf-8")
    assert (root / "_Маркировка" / "QR.png").exists() and project.folder_name == "100 Космос" and project.folder_synced_at
    imported = (await db_session.execute(select(Document).where(Document.title == "Подсунутый файл"))).scalars().all()
    assert len(imported) == 1 and imported[0].is_internal and cabinet.folder_name == "29_001 Насосная"
    assert project_doc.id and cabinet_doc.id


async def test_nightly_pass_syncs_relocates_and_survives_failures(db_session, nas, make_project, make_cabinet, monkeypatch):
    now = datetime.now(timezone.utc)
    alive = await make_project(name="26_100 Живой", production_number="26_100", warranty_ends_at=now + timedelta(days=90))
    expired = await make_project(name="26_101 Истёк", production_number="26_101", folder_name="101 Истёк",
                                 warranty_ends_at=now - timedelta(days=90))
    broken = await make_project(name="26_102 Сломанный", production_number="26_102", warranty_ends_at=now + timedelta(days=90))
    (nas.root / "101 Истёк").mkdir()
    real_sync = pfs.sync_project_folder

    async def flaky(session, project):
        if project.id == broken.id:
            raise OSError("шара недоступна")
        await real_sync(session, project)

    monkeypatch.setattr(pfs, "sync_project_folder", flaky)

    stats = await pfs._sync_all_projects(db_session)

    assert stats["failed"] == 1 and stats["synced"] >= 1 and stats["relocated"] >= 1 and stats["total"] >= 3
    assert (nas.root / "!2026" / "100 Живой").is_dir() and (nas.root / "!2026" / "101 Истёк").is_dir()
    assert not (nas.root / "101 Истёк").exists() and alive.id and expired.id

    await pfs.sync_all_project_folders()          # ночной вход тоже отрабатывает без исключений


# --- фоновые обёртки ---

async def test_background_wrappers_do_their_part(db_session, nas, make_user, make_chat, make_project, make_cabinet, make_document):
    project = await make_project(name="26_100 Космос", production_number="26_100")
    cabinet = await make_cabinet(project_id=project.id, object_number="29_001", admin_internal_name="Насосная")
    doc = await make_document(cabinet_id=cabinet.id, title="Паспорт ШУ", file_url=_upload(nas, "documents", "pc.pdf", b"%PDF-c"))
    root = nas.root / "!2026" / "100 Космос"

    pfs.schedule_folder_creation(project.id)
    pfs.schedule_folder_creation(999999)
    await _run_background(nas)
    assert (root / "_Маркировка" / "QR.png").exists()

    pfs.schedule_cabinet_folder(cabinet.id)
    await _run_background(nas)
    assert (root / "Фото" / "29_001 Насосная").is_dir()

    pfs.schedule_document_mirror(doc.id)
    await _run_background(nas)
    mirrored = root / "_Руководство" / "29_001 Насосная" / "Паспорт ШУ.pdf"
    assert mirrored.exists()

    pfs.schedule_document_removal(cabinet_id=cabinet.id, project_id=None, title="Паспорт ШУ", file_url=doc.file_url)
    await _run_background(nas)
    assert not mirrored.exists()

    photo_file = root / "Фото" / "29_001 Насосная" / "001 x.png"
    photo_file.write_bytes(PNG)
    pfs.schedule_photo_removal(cabinet_id=cabinet.id, project_id=None, nas_filename="001 x.png")
    pfs.schedule_photo_removal(cabinet_id=cabinet.id, project_id=None, nas_filename=None)
    await _run_background(nas)
    assert not photo_file.exists()

    pfs.schedule_folder_sync(project.id)
    pfs.schedule_folder_sync(999999)
    await _run_background(nas)
    assert project.folder_synced_at is not None


async def test_closed_request_chat_is_exported_on_closing(db_session, nas, make_user, make_chat, make_project, make_cabinet):
    project = await make_project(name="26_100 Космос", production_number="26_100")
    cabinet = await make_cabinet(project_id=project.id, object_number="29_001")
    owner = await make_user(full_name="Клиент")
    chat = await make_chat(owner, "cabinet", cabinet_id=cabinet.id)
    db_session.add(Message(chat_id=chat.id, sender_id=owner.id, text="Закрываем"))
    await db_session.flush()

    pfs.schedule_request_chat_export(chat.id)
    pfs.schedule_request_chat_export(999999)
    await _run_background(nas)

    exported = nas.root / "!2026" / "100 Космос" / "Переписка" / "29_001" / "Чат ШУ — Клиент.txt"
    assert "Закрываем" in exported.read_text(encoding="utf-8")


async def test_background_wrappers_ignore_missing_root_and_loose_cabinets(db_session, nas, make_cabinet, make_document, monkeypatch):
    loose = await make_cabinet()
    doc = await make_document(cabinet_id=loose.id, file_url=_upload(nas, "documents", "x.pdf"))

    pfs.schedule_cabinet_folder(loose.id)
    pfs.schedule_document_mirror(doc.id)
    pfs.schedule_document_removal(cabinet_id=loose.id, project_id=None, title="x", file_url=None)
    pfs.schedule_photo_removal(cabinet_id=loose.id, project_id=None, nas_filename="a.png")
    monkeypatch.setattr(settings, "project_folders_root", "")
    pfs.schedule_cabinet_folder(loose.id)
    pfs.schedule_request_chat_export(1)
    await _run_background(nas)

    assert list(nas.root.iterdir()) == []
