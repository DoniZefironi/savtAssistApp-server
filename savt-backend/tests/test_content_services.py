"""Сервисы контента: документы и фото (создание, правка, удаление, списки
сотрудника и клиента, заявки на доступ), база знаний (категории, статьи,
вложения) и FAQ. Базовые границы доступа к документам проверяет
test_document_access.py. Сохранение файлов, зеркалирование на NAS и
переиндексация бота подменяются и записываются."""
import io
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from starlette.datastructures import Headers

from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.models.audit_log import AuditLog
from app.models.cabinet_photo import CabinetPhoto
from app.models.document import Document
from app.models.document_request import DocumentRequest
from app.models.document_tag import DocumentTag
from app.models.embedding import EMBEDDING_DIM, Embedding
from app.models.faq_entry import FaqEntry
from app.models.kb_article_tag import KbArticleTag
from app.models.kbarticle import KbArticle
from app.models.notification import Notification
from app.models.tag import Tag
from app.models.user_favorite import UserFavorite
from app.schemas.documents import (
    ApproveDocumentRequestIn,
    DocumentUpdateIn,
    PhotoUpdateIn,
    RejectDocumentRequestIn,
)
from app.schemas.faq import (
    FaqCategoryCreateIn,
    FaqCategoryUpdateIn,
    FaqEntryCreateIn,
    FaqEntryUpdateIn,
)
from app.schemas.kb import (
    KbArticleCreateIn,
    KbArticleUpdateIn,
    KbCategoryCreateIn,
    KbCategoryUpdateIn,
)
from app.services import bot_indexer, document_service, kb_service, notification_service, project_folder_service
from app.services.document_service import AdminDocumentService, UserDocumentService
from app.services.faq_service import FaqCategoryService, FaqEntryService
from app.services.kb_service import KbArticleService, KbCategoryService
from app.services.upload_service import FileInfo


@pytest.fixture
def env(monkeypatch):
    e = SimpleNamespace(mirrored=[], removed_docs=[], removed_photos=[], reindexed=[], pushes=[], saved=[])

    async def save(file):
        e.saved.append(file.filename)
        return FileInfo(url=f"/static/files/{file.filename}", file_size_bytes=123,
                        mime_type=file.content_type or "application/pdf", doc_type="manual")

    async def push(session, user_id, title, body, data=None, notification_type=None):
        e.pushes.append(user_id)

    monkeypatch.setattr(document_service, "save_attachment_with_meta", save)
    monkeypatch.setattr(kb_service, "save_attachment_with_meta", save)
    monkeypatch.setattr(project_folder_service, "schedule_document_mirror", e.mirrored.append)
    monkeypatch.setattr(project_folder_service, "schedule_document_removal", lambda **kw: e.removed_docs.append(kw))
    monkeypatch.setattr(project_folder_service, "schedule_photo_removal", lambda **kw: e.removed_photos.append(kw))
    monkeypatch.setattr(bot_indexer, "schedule_reindex_document", e.reindexed.append)
    monkeypatch.setattr(notification_service, "send_push", push)
    return e


def _upload(name="manual.pdf", content_type="application/pdf"):
    return UploadFile(file=io.BytesIO(b"data"), filename=name, headers=Headers({"content-type": content_type}))


async def _embedding(db_session, source_type, source_id):
    db_session.add(Embedding(source_type=source_type, source_id=source_id, content="текст",
                             embedding=[0.0] * EMBEDDING_DIM))
    await db_session.flush()


async def _embeddings(db_session, source_type, source_id):
    return list((await db_session.execute(
        select(Embedding).where(Embedding.source_type == source_type, Embedding.source_id == source_id)
    )).scalars())


async def _audit(db_session, action):
    return list((await db_session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


# --- документы: сотрудник ---

async def test_create_document(db_session, env, make_user, make_project):
    admin = await make_user("admin")
    project = await make_project()

    out = await AdminDocumentService(db_session).create_document(
        _upload("Паспорт.pdf"), None, project.id, None, True, False, admin.id, "admin",
    )

    assert out.title == "Паспорт.pdf" and out.requires_approval is True and out.project_id == project.id
    assert out.file_size_bytes == 123 and env.mirrored == [out.id]
    [entry] = await _audit(db_session, "document.create")
    assert entry.payload["project_id"] == project.id


async def test_create_document_with_explicit_title(db_session, env, make_user, make_cabinet):
    admin = await make_user("admin")
    cabinet = await make_cabinet()

    out = await AdminDocumentService(db_session).create_document(
        _upload(), cabinet.id, None, "Схема подключения", False, True, admin.id, "admin",
    )

    assert out.title == "Схема подключения" and out.is_internal is True


async def test_update_document_flags_and_reindex(db_session, env, make_user, make_document):
    admin = await make_user("admin")
    doc = await make_document(is_internal=True)
    svc = AdminDocumentService(db_session)

    out = await svc.update_document(doc.id, DocumentUpdateIn(is_internal=False), admin.id, "admin")
    assert out.is_internal is False and env.reindexed == [doc.id]

    await svc.update_document(doc.id, DocumentUpdateIn(requires_approval=True), admin.id, "admin")
    assert env.reindexed == [doc.id]  # смена только доступа индекс не трогает

    await svc.update_document(doc.id, DocumentUpdateIn(), admin.id, "admin")  # пустая правка без записи
    assert len(await _audit(db_session, "document.update")) == 2
    with pytest.raises(NotFoundError):
        await svc.update_document(999999, DocumentUpdateIn(), admin.id, "admin")


async def test_admin_document_list_with_tags_and_filters(db_session, env, make_project, make_document):
    project = await make_project()
    manual = await make_document(project_id=project.id, doc_type="manual", requires_approval=False)
    secret = await make_document(project_id=project.id, doc_type="scheme", requires_approval=True)
    tag = Tag(name="важное", scope="document")
    db_session.add(tag)
    await db_session.flush()
    db_session.add(DocumentTag(document_id=manual.id, tag_id=tag.id))
    await db_session.flush()
    svc = AdminDocumentService(db_session)

    everything = await svc.list_documents(project_id=project.id)
    restricted = await svc.list_documents(project_id=project.id, requires_approval=True)
    by_type = await svc.list_documents(project_id=project.id, doc_type="manual")
    by_tag = await svc.list_documents(project_id=project.id, tag_ids=[tag.id])

    assert everything.total == 2
    assert [d.id for d in restricted.items] == [secret.id]
    assert [d.id for d in by_type.items] == [manual.id] == [d.id for d in by_tag.items]
    assert [t.name for t in by_tag.items[0].tags] == ["важное"]


async def test_delete_document_removes_embeddings_and_mirror(db_session, env, make_user, make_document):
    admin = await make_user("admin")
    doc = await make_document(title="Инструкция")
    other = await make_document()
    await _embedding(db_session, "document", doc.id)
    await _embedding(db_session, "document", other.id)
    doc_id, file_url = doc.id, doc.file_url

    await AdminDocumentService(db_session).delete_document(doc_id, admin.id, "admin")

    assert await db_session.get(Document, doc_id) is None
    assert await _embeddings(db_session, "document", doc_id) == []
    assert len(await _embeddings(db_session, "document", other.id)) == 1
    assert env.removed_docs[0]["title"] == "Инструкция" and env.removed_docs[0]["file_url"] == file_url
    with pytest.raises(NotFoundError):
        await AdminDocumentService(db_session).delete_document(doc_id, admin.id, "admin")


# --- фото ---

async def test_photo_lifecycle(db_session, env, make_cabinet):
    cabinet = await make_cabinet()
    svc = AdminDocumentService(db_session)

    photo = await svc.create_photo(_upload("щит.jpg", "image/jpeg"), cabinet.id, "Вид спереди", 2)
    page = await svc.list_photos(cabinet.id)
    updated = await svc.update_photo(photo.id, PhotoUpdateIn(caption="Вид сбоку"))
    await svc.delete_photo(photo.id)

    assert (photo.caption, photo.sort_order) == ("Вид спереди", 2) and [p.id for p in page.items] == [photo.id]
    assert updated.caption == "Вид сбоку" and updated.sort_order == 2
    assert (await svc.list_photos(cabinet.id)).total == 0
    assert env.removed_photos == [{"cabinet_id": cabinet.id, "project_id": None, "nas_filename": None}]
    for call in (lambda: svc.update_photo(photo.id, PhotoUpdateIn()), lambda: svc.delete_photo(photo.id)):
        with pytest.raises(NotFoundError):
            await call()


async def test_project_photos_are_separate_from_cabinet_photos(db_session, env, make_project, make_cabinet):
    project, cabinet = await make_project(), await make_cabinet()
    svc = AdminDocumentService(db_session)
    await svc.create_photo(_upload("a.jpg", "image/jpeg"), None, None, 0, project_id=project.id)
    await svc.create_photo(_upload("b.jpg", "image/jpeg"), cabinet.id, None, 0)

    assert (await svc.list_photos(None, project_id=project.id)).total == 1
    assert (await svc.list_photos(cabinet.id)).total == 1


# --- заявки на доступ: сотрудник ---

async def test_request_list_search_and_resolver_name(db_session, env, make_user, make_document):
    admin = await make_user("admin", full_name="Админ Анна")
    client = await make_user(full_name="Клиент Клиентов")
    doc = await make_document(requires_approval=True)
    users = UserDocumentService(db_session)
    req_id = await users.request_access(client.id, doc.id, "Нужен для монтажа")
    admins = AdminDocumentService(db_session)

    pending = await admins.list_requests(status="pending", search="Клиентов")
    assert [(r.id, r.user_message) for r in pending.items] == [(req_id, "Нужен для монтажа")]
    assert pending.items[0].user_is_verified is True

    await admins.approve_request(req_id, ApproveDocumentRequestIn(), admin.id, "admin")
    done = await admins.list_requests(status="approved", resolved_by_admin_id=admin.id)
    assert done.items[0].resolved_by_admin_name == "Админ Анна"
    note = (await db_session.execute(select(Notification).where(Notification.user_id == client.id))).scalar_one()
    assert note.title == "Доступ к документу открыт" and note.body == "Документ доступен для скачивания"


async def test_reject_request_notifies_with_reason(db_session, env, make_user, make_document):
    admin, client = await make_user("admin"), await make_user()
    doc = await make_document(requires_approval=True)
    req_id = await UserDocumentService(db_session).request_access(client.id, doc.id, None)

    await AdminDocumentService(db_session).reject_request(
        req_id, RejectDocumentRequestIn(admin_response="Не для вашей роли"), admin.id, "admin",
    )

    note = (await db_session.execute(select(Notification).where(Notification.user_id == client.id))).scalar_one()
    assert note.body == "Не для вашей роли"
    assert len(await _audit(db_session, "document_request.reject")) == 1
    assert len(await _audit(db_session, "document_request.create")) == 1


async def test_request_without_document_cannot_be_approved(db_session, env, make_user):
    admin, client = await make_user("admin"), await make_user()
    req = DocumentRequest(user_id=client.id, doc_type="manual")
    db_session.add(req)
    await db_session.flush()
    svc = AdminDocumentService(db_session)

    with pytest.raises(AlreadyExistsError):
        await svc.approve_request(req.id, ApproveDocumentRequestIn(), admin.id, "admin")
    with pytest.raises(NotFoundError):
        await svc.approve_request(999999, ApproveDocumentRequestIn(), admin.id, "admin")
    with pytest.raises(NotFoundError):
        await svc.reject_request(999999, RejectDocumentRequestIn(admin_response="x"), admin.id, "admin")


# --- документы: клиент ---

async def test_cabinet_documents_need_cabinet_access(
    db_session, env, make_user, make_cabinet, link_user_cabinet, make_document,
):
    owner, stranger = await make_user(), await make_user()
    cabinet = await make_cabinet()
    await link_user_cabinet(owner, cabinet)
    doc = await make_document(cabinet_id=cabinet.id, requires_approval=False)
    svc = UserDocumentService(db_session)

    page = await svc.list_documents(owner.id, cabinet_id=cabinet.id)

    assert [d.id for d in page.items] == [doc.id]
    with pytest.raises(PermissionDeniedError):
        await svc.list_documents(stranger.id, cabinet_id=cabinet.id)


async def test_user_document_list_marks_favorites_and_filters(
    db_session, env, make_user, make_project, link_user_project, make_document,
):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)
    liked = await make_document(project_id=project.id, doc_type="manual")
    await make_document(project_id=project.id, doc_type="scheme")
    db_session.add(UserFavorite(user_id=user.id, entity_type="document", entity_id=liked.id))
    await db_session.flush()
    svc = UserDocumentService(db_session)

    everything = await svc.list_project_documents(user.id, project.id)
    manuals = await svc.list_project_documents(user.id, project.id, doc_type="manual")

    assert {d.id: d.is_favorited for d in everything.items}[liked.id] is True
    assert sum(d.is_favorited for d in everything.items) == 1
    assert [d.id for d in manuals.items] == [liked.id]


# --- база знаний ---

async def test_kb_category_crud(db_session, env):
    svc = KbCategoryService(db_session)

    parent = await svc.create(KbCategoryCreateIn(name="Насосы", sort_order=2))
    child = await svc.create(KbCategoryCreateIn(name="Подбор насоса", parent_id=parent.id, description="Как выбрать"))
    renamed = await svc.update(parent.id, KbCategoryUpdateIn(name="Насосное оборудование"))

    assert parent.slug.startswith("насосы-") and renamed.name == "Насосное оборудование"
    assert [c.id for c in await svc.list_all(parent_id=parent.id)] == [child.id]
    assert [c.id for c in await svc.list_all(search="Подбор")] == [child.id]
    by_name = await svc.list_all(sort_by="name", sort_order="desc")
    assert [c.name for c in by_name] == sorted((c.name for c in by_name), reverse=True)
    with pytest.raises(NotFoundError):
        await svc.update(999999, KbCategoryUpdateIn(name="x"))
    with pytest.raises(NotFoundError):
        await svc.delete(999999)


async def test_kb_category_delete_cascades_to_articles_and_embeddings(db_session, env):
    cats, articles = KbCategoryService(db_session), KbArticleService(db_session)
    doomed = await cats.create(KbCategoryCreateIn(name="Удаляемая"))
    kept = await cats.create(KbCategoryCreateIn(name="Остаётся"))
    gone = await articles.create(KbArticleCreateIn(category_id=doomed.id, title="Пропадёт"))
    stays = await articles.create(KbArticleCreateIn(category_id=kept.id, title="Останется"))
    await _embedding(db_session, "kb_article", gone.id)
    await _embedding(db_session, "kb_article", stays.id)

    await cats.delete(doomed.id)

    assert await db_session.get(KbArticle, gone.id) is None and await db_session.get(KbArticle, stays.id) is not None
    assert await _embeddings(db_session, "kb_article", gone.id) == []
    assert len(await _embeddings(db_session, "kb_article", stays.id)) == 1


async def test_kb_article_lifecycle_and_publication(db_session, env, make_user):
    cat = await KbCategoryService(db_session).create(KbCategoryCreateIn(name="Общее"))
    svc = KbArticleService(db_session)

    created = await svc.create(KbArticleCreateIn(category_id=cat.id, title="Запуск насоса", description="Шаг 1"))
    assert created.description == "Шаг 1" and created.version == 1 and created.attachments == []

    edited = await svc.update(created.id, KbArticleUpdateIn(description="Шаг 1 и 2", title="Запуск насоса v2"))
    assert (edited.title, edited.description) == ("Запуск насоса v2", "Шаг 1 и 2")

    await svc.update(created.id, KbArticleUpdateIn(is_published=False))
    with pytest.raises(NotFoundError):
        await svc.get_detail(created.id)  # черновик клиенту не показывается
    await svc.update(created.id, KbArticleUpdateIn(is_published=True))
    assert (await svc.get_detail(created.id)).id == created.id

    await svc.delete(created.id)
    for call in (lambda: svc.get_detail(created.id), lambda: svc.update(created.id, KbArticleUpdateIn()),
                 lambda: svc.delete(created.id)):
        with pytest.raises(NotFoundError):
            await call()


async def test_kb_article_list_filters_tags_and_favorites(db_session, env, make_user):
    user = await make_user()
    cats = KbCategoryService(db_session)
    first_cat, second_cat = await cats.create(KbCategoryCreateIn(name="Первая")), await cats.create(KbCategoryCreateIn(name="Вторая"))
    svc = KbArticleService(db_session)
    a = await svc.create(KbArticleCreateIn(category_id=first_cat.id, title="Про насосы", description="Давление"))
    b = await svc.create(KbArticleCreateIn(category_id=second_cat.id, title="Про шкафы"))
    await svc.update(b.id, KbArticleUpdateIn(is_published=False))
    tag = Tag(name="насосы", scope="kb")
    db_session.add(tag)
    await db_session.flush()
    db_session.add_all([KbArticleTag(article_id=a.id, tag_id=tag.id), UserFavorite(user_id=user.id, entity_type="kb_article", entity_id=a.id)])
    await db_session.flush()

    published = await svc.list_articles(None, None, None, user_id=user.id)
    with_drafts = await svc.list_articles(None, None, None, is_published=None)
    by_category = await svc.list_articles(second_cat.id, None, None, is_published=None)
    by_tag = await svc.list_articles(None, [tag.id], None)
    found = await svc.list_articles(None, None, "давление")

    assert [i.id for i in published.items] == [a.id] and published.items[0].is_favorited is True
    assert {i.id for i in with_drafts.items} == {a.id, b.id}
    assert [i.id for i in by_category.items] == [b.id]
    assert [i.id for i in by_tag.items] == [a.id] and [t.name for t in by_tag.items[0].tags] == ["насосы"]
    assert [i.id for i in found.items] == [a.id]


async def test_kb_attachments(db_session, env):
    cat = await KbCategoryService(db_session).create(KbCategoryCreateIn(name="Общее"))
    svc = KbArticleService(db_session)
    article = await svc.create(KbArticleCreateIn(category_id=cat.id, title="Со вложением"))
    other = await svc.create(KbArticleCreateIn(category_id=cat.id, title="Другая"))

    att = await svc.add_attachment(article.id, _upload("схема.pdf"))
    listed = await svc.list_articles(None, None, None)
    path, mime, title = await svc.download_attachment(article.id, att.id)

    assert att.title == "схема.pdf" and att.file_size_bytes == 123
    assert {i.id: i.attachment_count for i in listed.items}[article.id] == 1
    assert (mime, title) == ("application/pdf", "схема.pdf") and path.name == "схема.pdf"
    assert [a.id for a in (await svc.get_detail(article.id)).attachments] == [att.id]

    with pytest.raises(NotFoundError):
        await svc.download_attachment(other.id, att.id)  # вложение чужой статьи
    with pytest.raises(NotFoundError):
        await svc.delete_attachment(other.id, att.id)
    with pytest.raises(NotFoundError):
        await svc.add_attachment(999999, _upload())
    await svc.delete_attachment(article.id, att.id)
    assert (await svc.get_detail(article.id)).attachments == []


async def test_kb_detail_shows_favorite_for_the_viewer(db_session, env, make_user):
    user = await make_user()
    cat = await KbCategoryService(db_session).create(KbCategoryCreateIn(name="Общее"))
    svc = KbArticleService(db_session)
    article = await svc.create(KbArticleCreateIn(category_id=cat.id, title="Любимая"))
    db_session.add(UserFavorite(user_id=user.id, entity_type="kb_article", entity_id=article.id))
    await db_session.flush()

    assert (await svc.get_detail(article.id, user.id)).is_favorited is True
    assert (await svc.get_detail(article.id)).is_favorited is False


# --- FAQ ---

async def test_faq_category_crud_and_cascade(db_session, env):
    cats, entries = FaqCategoryService(db_session), FaqEntryService(db_session)
    doomed = await cats.create(FaqCategoryCreateIn(name="Гарантия", sort_order=1))
    kept = await cats.create(FaqCategoryCreateIn(name="Доставка"))
    renamed = await cats.update(doomed.id, FaqCategoryUpdateIn(name="Гарантия и сервис"))
    gone = await entries.create(FaqEntryCreateIn(category_id=doomed.id, question="Как продлить гарантию?", answer="Через сервис"))
    stays = await entries.create(FaqEntryCreateIn(category_id=kept.id, question="Сколько ехать доставка?", answer="Неделя"))
    await _embedding(db_session, "faq", gone.id)
    await _embedding(db_session, "faq", stays.id)

    assert renamed.name == "Гарантия и сервис"
    assert [c.id for c in await cats.list_all(search="Доставка")] == [kept.id]

    await cats.delete(doomed.id)

    assert await db_session.get(FaqEntry, gone.id) is None
    assert await _embeddings(db_session, "faq", gone.id) == []
    for call in (lambda: cats.update(999999, FaqCategoryUpdateIn(name="x")), lambda: cats.delete(999999)):
        with pytest.raises(NotFoundError):
            await call()


async def test_faq_entry_version_grows_with_each_edit(db_session, env):
    cat = await FaqCategoryService(db_session).create(FaqCategoryCreateIn(name="Общее"))
    svc = FaqEntryService(db_session)
    entry = await svc.create(FaqEntryCreateIn(category_id=cat.id, question="Что такое ШУ?", answer="Шкаф управления"))

    first = await svc.update(entry.id, FaqEntryUpdateIn(answer="Шкаф управления насосами"))
    second = await svc.update(entry.id, FaqEntryUpdateIn(is_published=False))

    assert (entry.version, first.version, second.version) == (1, 2, 3)
    assert first.answer == "Шкаф управления насосами" and second.is_published is False
    await svc.delete(entry.id)
    for call in (lambda: svc.update(entry.id, FaqEntryUpdateIn()), lambda: svc.delete(entry.id)):
        with pytest.raises(NotFoundError):
            await call()


async def test_faq_entry_list_filters_and_favorites(db_session, env, make_user):
    user = await make_user()
    cats = FaqCategoryService(db_session)
    first_cat, second_cat = await cats.create(FaqCategoryCreateIn(name="Первая")), await cats.create(FaqCategoryCreateIn(name="Вторая"))
    svc = FaqEntryService(db_session)
    liked = await svc.create(FaqEntryCreateIn(category_id=first_cat.id, question="Как включить насос?", answer="Нажмите кнопку"))
    hidden = await svc.create(FaqEntryCreateIn(category_id=second_cat.id, question="Скрытый вопрос тут", answer="Ответ"))
    assert liked.is_published is False  # вопросы FAQ создаются черновиками
    await svc.update(liked.id, FaqEntryUpdateIn(is_published=True))
    db_session.add(UserFavorite(user_id=user.id, entity_type="faq_entry", entity_id=liked.id))
    await db_session.flush()

    everything = await svc.list_entries(None, None, user_id=user.id)
    published = await svc.list_entries(None, None, is_published=True)
    by_category = await svc.list_entries(second_cat.id, None)
    found = await svc.list_entries(None, "кнопку")

    assert everything.total == 2 and {e.id: e.is_favorited for e in everything.items} == {liked.id: True, hidden.id: False}
    assert [e.id for e in published.items] == [liked.id]
    assert [e.id for e in by_category.items] == [hidden.id]
    assert [e.id for e in found.items] == [liked.id]


@pytest.mark.parametrize("kind", ["kb", "faq"])
async def test_category_with_subcategories_cannot_be_deleted(db_session, env, kind):
    if kind == "kb":
        svc, make = KbCategoryService(db_session), lambda **kw: KbCategoryCreateIn(**kw)
    else:
        svc, make = FaqCategoryService(db_session), lambda **kw: FaqCategoryCreateIn(**kw)
    parent = await svc.create(make(name="Родитель"))
    child = await svc.create(make(name="Потомок", parent_id=parent.id))

    with pytest.raises(AlreadyExistsError):
        await svc.delete(parent.id)
    assert [c.id for c in await svc.list_all(parent_id=parent.id)] == [child.id]

    await svc.delete(child.id)
    await svc.delete(parent.id)
    assert await svc.list_all(search="Родитель") == []
