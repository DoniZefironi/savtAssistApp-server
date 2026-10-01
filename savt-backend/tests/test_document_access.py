"""Доступ к документам — две независимые границы безопасности:
1) requires_approval — документ виден в списке всем с доступом к проекту/ШУ,
   но file_url отдаётся только после одобрения заявки (и то только через
   GET /documents/{id}/download, никогда прямой ссылкой — см. get_file_path);
2) is_internal — служебный документ не виден пользователю вообще, как будто
   не существует (и в списке, и при запросе доступа к нему — NotFoundError,
   не PermissionDenied, чтобы по коду ответа нельзя было узнать о его
   существовании).

Ничего внешнего не трогает — NotificationService/send_push безопасны без
моков (см. test_phone_change_requests.py).
"""
import pytest

from app.core.exceptions import AlreadyExistsError, NotFoundError, PermissionDeniedError
from app.schemas.documents import ApproveDocumentRequestIn, RejectDocumentRequestIn
from app.services.document_service import AdminDocumentService, UserDocumentService


@pytest.fixture
def user_docs(db_session):
    return UserDocumentService(db_session)


@pytest.fixture
def admin_docs(db_session):
    return AdminDocumentService(db_session)


# --- list_documents: file_url виден только для свободных документов ---

async def test_free_document_file_url_visible_and_has_access_true(user_docs, make_user, make_project, link_user_project, make_document):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)
    doc = await make_document(project_id=project.id, requires_approval=False)

    page = await user_docs.list_project_documents(user.id, project.id)
    item = next(i for i in page.items if i.id == doc.id)
    assert item.file_url == doc.file_url
    assert item.has_access is True


async def test_restricted_document_hides_file_url_without_access(user_docs, make_user, make_project, link_user_project, make_document):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)
    doc = await make_document(project_id=project.id, requires_approval=True)

    page = await user_docs.list_project_documents(user.id, project.id)
    item = next(i for i in page.items if i.id == doc.id)
    assert item.file_url is None
    assert item.has_access is False


async def test_restricted_document_hides_file_url_even_after_access_granted(user_docs, admin_docs, make_user, make_project, link_user_project, make_document):
    # ключевое свойство: file_url НЕ появляется даже одобренным — подписанную
    # ссылку можно переслать третьему лицу, доступ проверяется на каждое
    # скачивание через get_file_path/download, не через список
    user = await make_user()
    admin = await make_user(role_name="admin")
    project = await make_project()
    await link_user_project(user, project)
    doc = await make_document(project_id=project.id, requires_approval=True)
    await admin_docs.doc_repo.grant_access(user.id, doc.id, admin.id)
    await admin_docs.session.commit()

    page = await user_docs.list_project_documents(user.id, project.id)
    item = next(i for i in page.items if i.id == doc.id)
    assert item.file_url is None  # по-прежнему null
    assert item.has_access is True  # но факт доступа отражён


async def test_internal_document_invisible_in_project_list(user_docs, make_user, make_project, link_user_project, make_document):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)
    await make_document(project_id=project.id, is_internal=True)

    page = await user_docs.list_project_documents(user.id, project.id)
    assert page.items == []


async def test_list_project_documents_requires_membership(user_docs, make_user, make_project, make_document):
    user = await make_user()
    project = await make_project()
    await make_document(project_id=project.id)
    # пользователь НЕ состоит в проекте

    with pytest.raises(PermissionDeniedError):
        await user_docs.list_project_documents(user.id, project.id)


# --- get_file_path: граница на скачивание ---

async def test_get_file_path_internal_document_is_not_found(user_docs, make_user, make_document):
    user = await make_user()
    doc = await make_document(is_internal=True)
    with pytest.raises(NotFoundError):
        await user_docs.get_file_path(user.id, doc.id)


async def test_get_file_path_restricted_without_access_denied(user_docs, make_user, make_document):
    user = await make_user()
    doc = await make_document(requires_approval=True)
    with pytest.raises(PermissionDeniedError):
        await user_docs.get_file_path(user.id, doc.id)


async def test_get_file_path_restricted_with_granted_access_succeeds(user_docs, admin_docs, make_user, make_document):
    user = await make_user()
    admin = await make_user(role_name="admin")
    doc = await make_document(requires_approval=True)
    await admin_docs.doc_repo.grant_access(user.id, doc.id, admin.id)
    await admin_docs.session.commit()

    path, mime_type, title = await user_docs.get_file_path(user.id, doc.id)
    assert mime_type == doc.mime_type
    assert title == doc.title


async def test_get_file_path_free_document_needs_no_access_grant(user_docs, make_user, make_document):
    user = await make_user()
    doc = await make_document(requires_approval=False)
    path, mime_type, title = await user_docs.get_file_path(user.id, doc.id)
    assert title == doc.title


# --- request_access ---

async def test_request_access_on_internal_document_not_found(user_docs, make_user, make_document):
    user = await make_user()
    doc = await make_document(is_internal=True)
    with pytest.raises(NotFoundError):
        await user_docs.request_access(user.id, doc.id, None)


async def test_request_access_on_free_document_rejected(user_docs, make_user, make_document):
    user = await make_user()
    doc = await make_document(requires_approval=False)
    with pytest.raises(AlreadyExistsError, match="доступен без запроса"):
        await user_docs.request_access(user.id, doc.id, None)


async def test_request_access_when_already_granted_rejected(user_docs, admin_docs, make_user, make_document):
    user = await make_user()
    admin = await make_user(role_name="admin")
    doc = await make_document(requires_approval=True)
    await admin_docs.doc_repo.grant_access(user.id, doc.id, admin.id)
    await admin_docs.session.commit()

    with pytest.raises(AlreadyExistsError, match="уже есть"):
        await user_docs.request_access(user.id, doc.id, None)


async def test_request_access_twice_rejected(user_docs, make_user, make_document):
    user = await make_user()
    doc = await make_document(requires_approval=True)
    await user_docs.request_access(user.id, doc.id, "нужен для проверки")

    with pytest.raises(AlreadyExistsError, match="уже отправлена"):
        await user_docs.request_access(user.id, doc.id, "ещё раз")


# --- approve/reject: заявка на доступ ---

async def test_approve_request_grants_access(user_docs, admin_docs, make_user, make_document):
    user = await make_user()
    admin = await make_user(role_name="admin")
    doc = await make_document(requires_approval=True)
    req_id = await user_docs.request_access(user.id, doc.id, None)

    await admin_docs.approve_request(req_id, ApproveDocumentRequestIn(admin_response="ок"), admin.id)

    assert await admin_docs.doc_repo.has_access(user.id, doc.id) is True


async def test_reject_request_does_not_grant_access(user_docs, admin_docs, make_user, make_document):
    user = await make_user()
    admin = await make_user(role_name="admin")
    doc = await make_document(requires_approval=True)
    req_id = await user_docs.request_access(user.id, doc.id, None)

    await admin_docs.reject_request(req_id, RejectDocumentRequestIn(admin_response="не обосновано"), admin.id)

    assert await admin_docs.doc_repo.has_access(user.id, doc.id) is False


async def test_approve_already_resolved_request_rejected(user_docs, admin_docs, make_user, make_document):
    user = await make_user()
    admin = await make_user(role_name="admin")
    doc = await make_document(requires_approval=True)
    req_id = await user_docs.request_access(user.id, doc.id, None)
    await admin_docs.approve_request(req_id, ApproveDocumentRequestIn(), admin.id)

    with pytest.raises(AlreadyExistsError, match="уже обработана"):
        await admin_docs.approve_request(req_id, ApproveDocumentRequestIn(), admin.id)
