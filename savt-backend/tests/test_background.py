"""spawn(): фоновая задача живёт до завершения, а её падение попадает в лог."""
import asyncio
import gc
import logging

from app.core import background


async def test_task_is_kept_until_it_finishes():
    release = asyncio.Event()
    done = []

    async def work():
        await release.wait()
        done.append(True)

    task = background.spawn(work())
    del task
    gc.collect()
    await asyncio.sleep(0)

    assert len(background._running) == 1  # ссылка держится, пока задача не закончилась

    release.set()
    await asyncio.sleep(0.01)

    assert done == [True]
    assert len(background._running) == 0


async def test_unhandled_error_is_logged_and_task_is_released(caplog):
    async def boom():
        raise RuntimeError("сломалось")

    with caplog.at_level(logging.ERROR, logger="app.core.background"):
        background.spawn(boom(), name="boom-task")
        await asyncio.sleep(0.01)

    assert "boom-task" in caplog.text
    assert "сломалось" in caplog.text
    assert len(background._running) == 0


async def test_cancelled_task_is_released_without_error_log(caplog):
    async def forever():
        await asyncio.sleep(60)

    with caplog.at_level(logging.ERROR, logger="app.core.background"):
        task = background.spawn(forever())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.01)

    assert caplog.text == ""
    assert len(background._running) == 0
