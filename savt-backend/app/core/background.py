import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Event loop хранит задачи только слабыми ссылками: фоновая задача, на которую
# нигде нет сильной ссылки, может быть уничтожена сборщиком мусора на
# середине работы. Поэтому все запущенные через spawn() держатся здесь, пока
# не завершатся
_running: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Запускает корутину в фоне "выстрелил и забыл": держит ссылку на задачу
    до её завершения и пишет в лог исключение, если корутина упала само и его
    никто не перехватил."""
    task = asyncio.create_task(coro, name=name)
    _running.add(task)
    task.add_done_callback(_finished)
    return task


def _finished(task: asyncio.Task) -> None:
    _running.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Фоновая задача %s упала", task.get_name(), exc_info=exc)
