"""Удалённый (soft-delete) проект: членство в нём остаётся в БД, но пользоваться
проектом уже нельзя — документы, чат, сервисные заявки и заявка на добавление ШУ
отвечают отказом. Для живого проекта те же вызовы проходят. Выйти из удалённого
проекта при этом по-прежнему можно (UserProjectRepository.find не смотрит на
удаление) — это проверено в test_direct_cabinet_access.py.
"""
from datetime import datetime, timezone

import pytest

from app.core.exceptions import PermissionDeniedError
from app.schemas.service_requests import ServiceRequestCreateIn
from app.services.chat_service import ChatService
from app.services.document_service import UserDocumentService
from app.services.service_request_service import ServiceRequestService
from app.services.user_cabinet_service import UserCabinetService


async def _member_of(make_user, make_project, link_user_project, deleted: bool):
    user = await make_user()
    project = await make_project(deleted_at=datetime.now(timezone.utc) if deleted else None)
    await link_user_project(user, project)
    return user, project


# --- документы проекта ---

async def test_project_documents_denied_for_deleted_project(db_session, make_user, make_project, link_user_project):
    user, project = await _member_of(make_user, make_project, link_user_project, deleted=True)
    with pytest.raises(PermissionDeniedError):
        await UserDocumentService(db_session).list_project_documents(user_id=user.id, project_id=project.id)


async def test_project_documents_allowed_for_live_project(db_session, make_user, make_project, link_user_project):
    user, project = await _member_of(make_user, make_project, link_user_project, deleted=False)
    page = await UserDocumentService(db_session).list_project_documents(user_id=user.id, project_id=project.id)
    assert page.total == 0


# --- чат проекта ---

async def test_project_chat_denied_for_deleted_project(db_session, make_user, make_project, link_user_project):
    user, project = await _member_of(make_user, make_project, link_user_project, deleted=True)
    with pytest.raises(PermissionDeniedError):
        await ChatService(db_session).get_project_chat(user.id, project.id)


async def test_project_chat_allowed_for_live_project(db_session, make_user, make_project, link_user_project):
    user, project = await _member_of(make_user, make_project, link_user_project, deleted=False)
    chat = await ChatService(db_session).get_project_chat(user.id, project.id)
    assert chat.project_id == project.id


# --- сервисная заявка по проекту ---

async def test_service_request_for_deleted_project_denied(db_session, make_user, make_project, link_user_project):
    user, project = await _member_of(make_user, make_project, link_user_project, deleted=True)
    with pytest.raises(PermissionDeniedError):
        await ServiceRequestService(db_session).create(user.id, ServiceRequestCreateIn(
            project_id=project.id, request_type="diagnostics", description="Проблема по всему проекту",
        ))


async def test_service_request_for_live_project_allowed(db_session, make_user, make_project, link_user_project):
    user, project = await _member_of(make_user, make_project, link_user_project, deleted=False)
    req = await ServiceRequestService(db_session).create(user.id, ServiceRequestCreateIn(
        project_id=project.id, request_type="diagnostics", description="Проблема по всему проекту",
    ))
    assert req.project_id == project.id


# --- заявка на добавление ШУ в проект ---

async def test_add_by_photo_denied_for_deleted_project(db_session, make_user, make_project, link_user_project):
    user, project = await _member_of(make_user, make_project, link_user_project, deleted=True)
    with pytest.raises(PermissionDeniedError):
        await UserCabinetService(db_session).add_by_photo(user.id, project.id, "/static/photos/a.jpg", None)


async def test_add_by_photo_allowed_for_live_project(db_session, make_user, make_project, link_user_project):
    user, project = await _member_of(make_user, make_project, link_user_project, deleted=False)
    request_id = await UserCabinetService(db_session).add_by_photo(user.id, project.id, "/static/photos/a.jpg", None)
    assert request_id > 0
