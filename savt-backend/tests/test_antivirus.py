"""Проверка файлов антивирусом ClamAV: сам протокол демона (команда, куски с длиной, нулевой
хвост, разбор ответа), поведение при заражённом и чистом файле, при выключенном и недоступном
антивирусе и подключение к загрузке файлов. Демон подменён небольшим сервером на сокете,
который записывает всё, что ему прислали, и отвечает заданной строкой."""
import asyncio
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services import antivirus_service, upload_service
from tests.test_uploads import _upload

EICAR = "stream: Eicar-Test-Signature FOUND\0"
CLEAN = "stream: OK\0"


@pytest.fixture
async def clamd(monkeypatch):
    """Фальшивый clamd: принимает INSTREAM, запоминает команду и куски, отвечает state.reply."""
    state = SimpleNamespace(reply=CLEAN, command=None, chunks=[], connections=0)

    async def handle(reader, writer):
        state.connections += 1
        state.command = await reader.readuntil(b"\0")
        while True:
            (length,) = struct.unpack("!I", await reader.readexactly(4))
            if length == 0:
                break
            state.chunks.append(await reader.readexactly(length))
        writer.write(state.reply.encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(settings, "clamav_enabled", True)
    monkeypatch.setattr(settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(settings, "clamav_port", port)
    monkeypatch.setattr(settings, "clamav_fail_closed", False)
    yield state
    server.close()
    await server.wait_closed()


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(upload_service, "UPLOAD_ROOT", root)
    return root


def _file(tmp_path: Path, data: bytes = b"content") -> Path:
    path = tmp_path / "f.bin"
    path.write_bytes(data)
    return path


# --- протокол ---

async def test_file_is_streamed_to_the_daemon_in_length_prefixed_chunks(clamd, tmp_path, monkeypatch):
    monkeypatch.setattr(antivirus_service, "_CHUNK_SIZE", 4)

    result = await antivirus_service.scan_file(_file(tmp_path, b"0123456789"))

    assert result.infected is False
    assert clamd.command == b"zINSTREAM\0"
    assert clamd.chunks == [b"0123", b"4567", b"89"]          # каждый кусок ушёл с 4-байтной длиной, затем нулевая


async def test_an_infected_reply_names_the_threat(clamd, tmp_path):
    clamd.reply = EICAR

    result = await antivirus_service.scan_file(_file(tmp_path))

    assert result.infected is True and result.signature == "Eicar-Test-Signature"


@pytest.mark.parametrize("reply", ["INSTREAM size limit exceeded. ERROR\0", "", "что-то непонятное\0"])
async def test_an_error_or_garbage_reply_means_the_scan_did_not_happen(clamd, tmp_path, reply):
    clamd.reply = reply

    with pytest.raises(antivirus_service.AntivirusUnavailable):
        await antivirus_service.scan_file(_file(tmp_path))


async def test_a_dead_daemon_means_the_scan_did_not_happen(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(settings, "clamav_port", 1)             # там никто не слушает

    with pytest.raises(antivirus_service.AntivirusUnavailable):
        await antivirus_service.scan_file(_file(tmp_path))


async def test_a_silent_daemon_is_cut_off_by_the_timeout(tmp_path, monkeypatch):
    async def silent(reader, writer):
        await asyncio.sleep(5)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    monkeypatch.setattr(settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(settings, "clamav_port", server.sockets[0].getsockname()[1])
    monkeypatch.setattr(settings, "clamav_timeout_seconds", 0.2)

    with pytest.raises(antivirus_service.AntivirusUnavailable):
        await antivirus_service.scan_file(_file(tmp_path))
    server.close()


# --- ensure_clean: что делать с результатом ---

async def test_when_disabled_nothing_is_scanned(clamd, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "clamav_enabled", False)
    clamd.reply = EICAR

    await antivirus_service.ensure_clean(_file(tmp_path))

    assert clamd.connections == 0


async def test_a_clean_file_passes(clamd, tmp_path):
    await antivirus_service.ensure_clean(_file(tmp_path))

    assert clamd.connections == 1


async def test_an_infected_file_is_rejected_with_the_threat_name(clamd, tmp_path):
    clamd.reply = EICAR

    with pytest.raises(HTTPException) as err:
        await antivirus_service.ensure_clean(_file(tmp_path))

    assert err.value.status_code == 422 and "Eicar-Test-Signature" in err.value.detail


async def test_an_unavailable_antivirus_lets_the_file_through_but_logs_it(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "clamav_enabled", True)
    monkeypatch.setattr(settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(settings, "clamav_port", 1)
    monkeypatch.setattr(settings, "clamav_fail_closed", False)

    await antivirus_service.ensure_clean(_file(tmp_path))

    assert "Проверка файла антивирусом не выполнена" in caplog.text


async def test_a_strict_setting_refuses_the_upload_when_the_antivirus_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "clamav_enabled", True)
    monkeypatch.setattr(settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(settings, "clamav_port", 1)
    monkeypatch.setattr(settings, "clamav_fail_closed", True)

    with pytest.raises(HTTPException) as err:
        await antivirus_service.ensure_clean(_file(tmp_path))

    assert err.value.status_code == 503


# --- загрузка файлов ---

async def test_an_infected_upload_is_rejected_and_leaves_nothing_on_disk(clamd, storage):
    clamd.reply = EICAR

    with pytest.raises(HTTPException) as err:
        await upload_service.save_attachment_with_meta(_upload("report.pdf", "application/pdf", b"data"))

    assert err.value.status_code == 422
    assert [path for path in storage.rglob("*") if path.is_file()] == []


async def test_a_clean_upload_is_saved_and_was_scanned_with_its_content(clamd, storage):
    info = await upload_service.save_attachment_with_meta(_upload("report.pdf", "application/pdf", b"data"))

    assert (storage / info.url.removeprefix("/static/")).read_bytes() == b"data"
    assert b"".join(clamd.chunks) == b"data"


async def test_voice_messages_are_scanned_too(clamd, storage):
    clamd.reply = EICAR

    with pytest.raises(HTTPException) as err:
        await upload_service.save_voice(_upload("v.ogg", "audio/ogg", b"voice"))

    assert err.value.status_code == 422
    assert [path for path in storage.rglob("*") if path.is_file()] == []
