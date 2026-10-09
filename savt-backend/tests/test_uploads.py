"""Загрузка и выдача файлов: сохранение вложений и голосовых по типам, лимиты размера,
копирование файла с NAS, удаление, распознавание голоса и скачивание по подписанной
ссылке с защитой от выхода за каталог загрузок. Файлы пишутся во временный каталог,
ffmpeg и распознавание речи подменены."""
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.config import settings
from app.core.security import create_access_token
from app.core.signed_urls import sign_url
from app.routers import upload as upload_router
from app.services import upload_service, yandex_service


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(upload_service, "UPLOAD_ROOT", root)
    monkeypatch.setattr(upload_router, "UPLOAD_ROOT", root)
    monkeypatch.setattr(settings, "static_link_secret", "test-secret")
    return root


def _upload(name="a.pdf", content_type="application/pdf", data=b"data"):
    return UploadFile(file=io.BytesIO(data), filename=name, headers=Headers({"content-type": content_type}))


def _auth(user, role="user"):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


def _path_of(storage, url):
    return storage / url.removeprefix("/static/")


# --- сохранение вложений ---

@pytest.mark.parametrize("mime,folder,ext,doc_type", [
    ("application/pdf", "documents", "pdf", "pdf"),
    ("image/jpeg", "photos", "jpg", "photo"),
    ("video/mp4", "videos", "mp4", "video"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "documents", "xlsx", "excel"),
    ("image/gif", "photos", "gif", "other"),
    ("video/x-matroska", "videos", "x-matroska", "other"),
])
async def test_attachment_goes_to_its_folder_with_metadata(storage, mime, folder, ext, doc_type):
    info = await upload_service.save_attachment_with_meta(_upload("f", mime, b"12345"))

    assert info.url.startswith(f"/static/{folder}/") and info.mime_type == mime and info.doc_type == doc_type
    assert info.file_size_bytes == 5 and _path_of(storage, info.url).read_bytes() == b"12345"
    assert info.url.endswith(".bin") if ext == "x-matroska" else info.url.endswith(f".{ext}")


async def test_unknown_types_keep_a_safe_extension(storage):
    named = await upload_service.save_attachment_with_meta(_upload("Схема.DWG", "application/octet-stream"))
    nameless = await upload_service.save_attachment_with_meta(_upload(None, "application/octet-stream"))
    suspicious = await upload_service.save_attachment_with_meta(_upload("x.p/../hp", "text/plain"))
    audio = await upload_service.save_attachment_with_meta(_upload("a.ogg", "audio/ogg"))

    assert named.url.startswith("/static/files/") and named.url.endswith(".dwg")
    assert nameless.url.endswith(".bin") and suspicious.url.endswith(".bin")
    assert audio.url.startswith("/static/voices/")
    assert upload_service._sanitize_ext(" JPG ") == "jpg" and upload_service._sanitize_ext("a/b") == "bin"
    assert (await upload_service.save_attachment(_upload())).startswith("/static/documents/")


async def test_oversized_attachment_is_refused_and_leaves_no_file(storage, monkeypatch):
    monkeypatch.setattr(upload_service, "MAX_ATTACHMENT_SIZE", 4)

    with pytest.raises(HTTPException) as err:
        await upload_service.save_attachment_with_meta(_upload("big.pdf", "application/pdf", b"123456"))

    assert err.value.status_code == 413
    assert list(storage.rglob("*.pdf")) == []


async def test_voice_accepts_only_audio_formats(storage):
    url = await upload_service.save_voice(_upload("v.webm", "audio/webm", b"voice"))

    assert url.startswith("/static/voices/") and url.endswith(".webm")
    with pytest.raises(HTTPException) as err:
        await upload_service.save_voice(_upload("v.txt", "text/plain"))
    assert err.value.status_code == 415


async def test_oversized_voice_is_refused(monkeypatch):
    monkeypatch.setattr(upload_service, "MAX_VOICE_SIZE", 3)

    with pytest.raises(HTTPException) as err:
        await upload_service.save_voice(_upload("v.ogg", "audio/ogg", b"12345"))

    assert err.value.status_code == 413


# --- файлы с NAS и удаление ---

def test_local_file_is_copied_with_the_same_metadata(storage, tmp_path):
    source = tmp_path / "Паспорт.pdf"
    source.write_bytes(b"%PDF-1")
    photo = tmp_path / "snimok.png"
    photo.write_bytes(b"png")
    odd = tmp_path / "data.xyz123"
    odd.write_bytes(b"?")
    others = {}
    for name, folder in (("anim.gif", "photos"), ("clip.mpeg", "videos"), ("sound.mp3", "voices")):
        path = tmp_path / name
        path.write_bytes(b"x")
        others[name] = folder

    pdf_info = upload_service.save_local_file(source)
    photo_info = upload_service.save_local_file(photo)
    odd_info = upload_service.save_local_file(odd)

    assert pdf_info.url.startswith("/static/documents/") and pdf_info.doc_type == "pdf" and pdf_info.file_size_bytes == 6
    assert photo_info.url.startswith("/static/photos/") and photo_info.doc_type == "photo"
    assert odd_info.url.startswith("/static/files/") and odd_info.url.endswith(".xyz123")
    assert _path_of(storage, pdf_info.url).read_bytes() == b"%PDF-1" and source.exists()
    for name, folder in others.items():
        assert upload_service.save_local_file(tmp_path / name).url.startswith(f"/static/{folder}/")


def test_delete_removes_only_files_inside_the_upload_root(storage, tmp_path):
    inside = storage / "photos" / "a.jpg"
    inside.parent.mkdir()
    inside.write_bytes(b"x")
    outside = tmp_path / "secret.txt"
    outside.write_text("не трогать")

    upload_service.delete_uploaded_file("/static/photos/a.jpg")
    upload_service.delete_uploaded_file("/static/photos/a.jpg")        # повторно — не ошибка
    upload_service.delete_uploaded_file("/static/../secret.txt")
    upload_service.delete_uploaded_file(None)

    assert not inside.exists() and outside.exists()


# --- перекодирование голоса ---

def test_transcoding_returns_ffmpeg_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"OggS"))

    assert upload_service.transcode_to_ogg_opus(b"raw") == b"OggS"


@pytest.mark.parametrize("failure", ["bad-format", "empty", "timeout"])
def test_transcoding_failures_become_400(monkeypatch, failure):
    def fake_run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ffmpeg", 60)
        return SimpleNamespace(returncode=1 if failure == "bad-format" else 0, stdout=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(HTTPException) as err:
        upload_service.transcode_to_ogg_opus(b"raw")
    assert err.value.status_code == 400


# --- HTTP ---

async def test_upload_endpoints_return_signed_urls(api, make_user, storage):
    user = await make_user()
    pdf = {"file": ("Паспорт.pdf", io.BytesIO(b"data"), "application/pdf")}

    attachment = await api.post("/upload/attachment", headers=_auth(user), files=pdf)
    voice = await api.post("/upload/voice", headers=_auth(user), files={"file": ("v.ogg", io.BytesIO(b"v"), "audio/ogg")})
    bad_voice = await api.post("/upload/voice", headers=_auth(user), files={"file": ("v.txt", io.BytesIO(b"v"), "text/plain")})
    anonymous = await api.post("/upload/attachment", files=pdf)

    assert attachment.status_code == 200 and "md5=" in attachment.json()["url"] and "expires=" in attachment.json()["url"]
    assert _path_of(storage, attachment.json()["url"].split("?")[0]).exists()
    assert voice.status_code == 200 and voice.json()["url"].startswith("/static/voices/")
    assert bad_voice.status_code == 415 and anonymous.status_code in (401, 403)


async def test_download_returns_the_file_as_an_attachment(api, make_user, storage):
    user = await make_user()
    target = storage / "documents" / "doc.pdf"
    target.parent.mkdir()
    target.write_bytes(b"%PDF content")

    response = await api.get("/upload/download", params={"url": sign_url("/static/documents/doc.pdf")}, headers=_auth(user))

    assert response.status_code == 200 and response.content == b"%PDF content"
    assert 'attachment; filename="doc.pdf"' in response.headers["content-disposition"]


@pytest.mark.parametrize("url,expected", [
    ("/static/documents/doc.pdf", 403),                                  # без подписи
    ("/static/documents/doc.pdf?md5=deadbeef&expires=1", 403),            # подпись протухла и неверна
    (None, 404),                                                          # подпись верна, файла нет (None → подписать missing)
])
async def test_download_rejects_unsigned_and_missing_files(api, make_user, storage, url, expected):
    user = await make_user()
    if url is None:
        url = sign_url("/static/documents/missing.pdf")

    response = await api.get("/upload/download", params={"url": url}, headers=_auth(user))

    assert response.status_code == expected


async def test_download_cannot_leave_the_upload_root(api, make_user, storage, tmp_path):
    user = await make_user()
    (tmp_path / "secret.txt").write_text("секрет")

    escape = await api.get("/upload/download", params={"url": sign_url("/static/../secret.txt")}, headers=_auth(user))
    foreign_prefix = await api.get("/upload/download", params={"url": sign_url("/etc/passwd")}, headers=_auth(user))

    assert escape.status_code == 400 and b"\xd1\x81\xd0\xb5\xd0\xba\xd1\x80\xd0\xb5\xd1\x82" not in escape.content
    assert foreign_prefix.status_code in (400, 403)


async def test_download_requires_authorization(api, storage):
    assert (await api.get("/upload/download", params={"url": "/static/x"})).status_code in (401, 403)


# --- распознавание голоса ---

@pytest.fixture
def speech(monkeypatch, storage):
    calls = SimpleNamespace(short=[], long=[], error=None)
    (storage / "voices").mkdir()
    (storage / "voices" / "v.ogg").write_bytes(b"raw")

    async def short(audio, format):
        if calls.error:
            raise calls.error
        calls.short.append(audio)
        return "Не работает насос"

    async def long(audio, format):
        calls.long.append(audio)
        return "Длинная запись"

    monkeypatch.setattr(upload_router, "transcode_to_ogg_opus", lambda raw: b"opus:" + raw)
    monkeypatch.setattr(yandex_service, "transcribe_voice", short)
    monkeypatch.setattr(yandex_service, "transcribe_voice_long", long)
    return calls


async def test_transcribe_returns_the_text(api, make_user, speech):
    user = await make_user()

    response = await api.post("/upload/transcribe", headers=_auth(user), json={"file_url": sign_url("/static/voices/v.ogg")})

    assert response.status_code == 200 and response.json() == {"text": "Не работает насос"}
    assert speech.short == [b"opus:raw"] and speech.long == []


async def test_long_recordings_use_the_long_recognition(api, make_user, speech, monkeypatch):
    monkeypatch.setattr(yandex_service, "MAX_SYNC_STT_BYTES", 3)
    user = await make_user()

    response = await api.post("/upload/transcribe", headers=_auth(user), json={"file_url": sign_url("/static/voices/v.ogg")})

    assert response.json() == {"text": "Длинная запись"} and speech.short == []


async def test_recognition_failure_is_a_503_and_bad_links_are_refused(api, make_user, speech):
    user = await make_user()
    speech.error = RuntimeError("SpeechKit недоступен")

    failed = await api.post("/upload/transcribe", headers=_auth(user), json={"file_url": sign_url("/static/voices/v.ogg")})
    unsigned = await api.post("/upload/transcribe", headers=_auth(user), json={"file_url": "/static/voices/v.ogg"})

    assert failed.status_code == 503 and "SpeechKit" in failed.json()["detail"]
    assert unsigned.status_code == 403
