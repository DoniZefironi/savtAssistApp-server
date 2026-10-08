"""Публичная страница для QR, отсканированного обычной камерой (/add/project/{код},
/add/cabinet/{код}) и то, что реально зашивается в сами QR. Главное, что здесь
закреплено: fallback у intent:// не ведёт на саму страницу (иначе бесконечный
редирект и 429), путь в intent:// — в формате, который разбирает приложение
(savt://project/{код}), а в адрес QR не попадает двойной слеш при слеше в
PUBLIC_BASE_URL.
"""
import pytest

from app.config import settings
from app.routers import add_qr
from app.routers import qr as qr_router
from app.services import project_folder_service

APK = "https://drive.example/uc?export=download&id=FILE"
PACKAGE = "com.test.app"


@pytest.fixture
def intent_enabled(monkeypatch):
    monkeypatch.setattr(settings, "android_package_name", PACKAGE)
    monkeypatch.setattr(settings, "apk_download_url", APK)


def _body(response) -> str:
    return response.body.decode("utf-8")


# --- _intent_url ---

def test_intent_url_uses_savt_path_and_apk_as_fallback(intent_enabled):
    url = add_qr._intent_url("project/ABC")

    assert url.startswith("intent://project/ABC#Intent;scheme=savt;")
    assert f"package={PACKAGE};" in url
    assert url.endswith(f"S.browser_fallback_url={APK};end")


def test_intent_url_fallback_never_points_to_landing_page(intent_enabled):
    fallback = add_qr._intent_url("project/ABC").split("S.browser_fallback_url=")[1]

    assert "/add/" not in fallback


def test_intent_url_skipped_without_package(monkeypatch):
    monkeypatch.setattr(settings, "android_package_name", "")
    monkeypatch.setattr(settings, "apk_download_url", APK)
    assert add_qr._intent_url("project/ABC") is None


def test_intent_url_skipped_without_apk_link(monkeypatch):
    # некуда уводить при неудаче — лучше вообще не пытаться открывать приложение
    monkeypatch.setattr(settings, "android_package_name", PACKAGE)
    monkeypatch.setattr(settings, "apk_download_url", "")
    assert add_qr._intent_url("project/ABC") is None


# --- страница проекта ---

async def test_project_page_shows_name_and_tries_to_open_app(db_session, make_project, intent_enabled):
    await make_project(name="Бизнес-центр Космос", unique_code="page-proj-1")

    body = _body(await add_qr.add_project_landing("page-proj-1", session=db_session))

    assert "Проект «Бизнес-центр Космос»" in body
    assert "intent://project/page-proj-1#Intent;" in body
    assert "Скачать приложение" in body


async def test_project_page_unknown_code_has_no_intent(db_session, intent_enabled):
    body = _body(await add_qr.add_project_landing("nope", session=db_session))

    assert "Проект не найден" in body
    assert "intent://" not in body


async def test_project_page_escapes_project_name(db_session, make_project):
    await make_project(name="<script>alert(1)</script>", unique_code="page-proj-xss")

    body = _body(await add_qr.add_project_landing("page-proj-xss", session=db_session))

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


async def test_page_without_apk_link_says_app_not_published(db_session, make_project, monkeypatch):
    monkeypatch.setattr(settings, "apk_download_url", "")
    await make_project(unique_code="page-proj-2")

    body = _body(await add_qr.add_project_landing("page-proj-2", session=db_session))

    assert "Скачать приложение" not in body
    assert "пока не опубликовано" in body


# --- страница ШУ ---

async def test_cabinet_page_shows_name_and_cabinet_intent(db_session, make_cabinet, intent_enabled):
    await make_cabinet(admin_internal_name="ШУ-Котельная", unique_code="page-cab-1")

    body = _body(await add_qr.add_cabinet_landing("page-cab-1", session=db_session))

    assert "ШУ «ШУ-Котельная»" in body
    assert "intent://cabinet/page-cab-1#Intent;" in body


async def test_cabinet_page_falls_back_to_object_number_when_no_internal_name(db_session, make_cabinet):
    cabinet = await make_cabinet(admin_internal_name=None, unique_code="page-cab-2")

    body = _body(await add_qr.add_cabinet_landing("page-cab-2", session=db_session))

    assert f"ШУ «{cabinet.object_number}»" in body


async def test_cabinet_page_unknown_code(db_session, intent_enabled):
    body = _body(await add_qr.add_cabinet_landing("nope", session=db_session))

    assert "ШУ не найден" in body
    assert "intent://" not in body


# --- что зашито в QR ---

async def test_project_qr_encodes_public_url_without_double_slash(db_session, make_project, monkeypatch):
    captured = []
    monkeypatch.setattr(qr_router, "generate_qr", lambda data: captured.append(data) or b"png")
    monkeypatch.setattr(settings, "public_base_url", "https://qr.example/")
    project = await make_project(unique_code="qr-proj-1")

    await qr_router.get_project_qr(project_id=project.id, _=None, session=db_session)

    assert captured == ["https://qr.example/add/project/qr-proj-1"]


async def test_cabinet_qr_encodes_public_url(db_session, make_cabinet, monkeypatch):
    captured = []
    monkeypatch.setattr(qr_router, "generate_qr", lambda data: captured.append(data) or b"png")
    monkeypatch.setattr(settings, "public_base_url", "https://qr.example")
    cabinet = await make_cabinet(unique_code="qr-cab-1")

    await qr_router.get_cabinet_qr(cabinet_id=cabinet.id, _=None, session=db_session)

    assert captured == ["https://qr.example/add/cabinet/qr-cab-1"]


async def test_nas_project_qr_encodes_public_url(make_project, monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(project_folder_service, "generate_qr", lambda data: captured.append(data) or b"png")
    monkeypatch.setattr(settings, "public_base_url", "https://qr.example/")
    (tmp_path / "_Маркировка").mkdir()
    project = await make_project(unique_code="qr-nas-1")

    await project_folder_service.write_project_qr(tmp_path, project)

    assert captured == ["https://qr.example/add/project/qr-nas-1"]
