"""Проверка загруженных файлов антивирусом ClamAV (контейнер clamav, демон clamd).

Файл передаётся демону по его протоколу INSTREAM (команда, затем куски файла, каждый с
4-байтной длиной, и нулевая длина в конце), ответ приходит одной строкой:
`stream: OK`, `stream: <название вируса> FOUND` или `… ERROR`. Отдельной библиотеки для этого
не нужно, протокол короткий."""
import asyncio
import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024  # 1 МБ


class AntivirusUnavailable(Exception):
    """Демон не отвечает, оборвал соединение или ответил ошибкой (например, файл больше его лимита)."""


@dataclass
class ScanResult:
    infected: bool
    signature: str | None = None


def _parse_reply(reply: str) -> ScanResult:
    text = reply.strip("\0 \n")
    if text.endswith("FOUND"):
        signature = text.removeprefix("stream:").removesuffix("FOUND").strip()
        return ScanResult(infected=True, signature=signature or "неизвестная угроза")
    if text.endswith("OK"):
        return ScanResult(infected=False)
    raise AntivirusUnavailable(f"неожиданный ответ ClamAV: {text or 'пустой ответ'}")


async def _stream_to_clamd(path: Path) -> str:
    reader, writer = await asyncio.open_connection(settings.clamav_host, settings.clamav_port)
    try:
        writer.write(b"zINSTREAM\0")
        with path.open("rb") as source:
            while chunk := await asyncio.to_thread(source.read, _CHUNK_SIZE):
                writer.write(struct.pack("!I", len(chunk)) + chunk)
                await writer.drain()
        writer.write(struct.pack("!I", 0))
        await writer.drain()
        reply = await reader.read(4096)
        return reply.decode("utf-8", errors="replace")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def scan_file(path: Path) -> ScanResult:
    """Проверяет файл на диске. Недоступность демона и любые его ошибки — AntivirusUnavailable."""
    try:
        reply = await asyncio.wait_for(_stream_to_clamd(path), timeout=settings.clamav_timeout_seconds)
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
        raise AntivirusUnavailable(f"ClamAV недоступен: {exc!r}") from exc
    return _parse_reply(reply)


async def ensure_clean(path: Path) -> None:
    """Пропускает чистый файл. Заражённый — HTTP 422 (удалять файл с диска должен вызывающий).
    Если антивирус выключен, ничего не делает; если недоступен — по настройке clamav_fail_closed
    либо принимает файл с записью ошибки в лог, либо отклоняет загрузку (503)."""
    if not settings.clamav_enabled:
        return
    try:
        result = await scan_file(path)
    except AntivirusUnavailable as exc:
        logger.error("Проверка файла антивирусом не выполнена (%s): %s", path.name, exc)
        if settings.clamav_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Проверка файла на вирусы сейчас недоступна, попробуйте ещё раз позже",
            ) from exc
        return
    if result.infected:
        logger.warning("Антивирус отклонил файл %s: %s", path.name, result.signature)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Файл отклонён антивирусом: {result.signature}. Загрузите другой файл",
        )
