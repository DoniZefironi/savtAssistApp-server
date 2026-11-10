"""Планировщик фоновых задач: задача, у которой срок наступил, пока цикл событий был занят,
всё равно выполняется (допуск опоздания и объединение пропущенных срабатываний)."""
import asyncio
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.main import SCHEDULER_JOB_DEFAULTS


async def _job_ran_after_a_blocked_loop(**scheduler_kwargs) -> bool:
    ran = []

    async def job():
        ran.append(1)

    scheduler = AsyncIOScheduler(**scheduler_kwargs)
    scheduler.start()
    try:
        scheduler.add_job(job, "date", run_date=datetime.now() + timedelta(seconds=0.2))
        time.sleep(1.6)                    # тяжёлая работа в цикле в момент срока задачи
        await asyncio.sleep(0.5)
    finally:
        scheduler.shutdown(wait=False)
    return bool(ran)


def test_job_defaults_allow_a_late_run_and_merge_missed_ones():
    assert SCHEDULER_JOB_DEFAULTS["misfire_grace_time"] >= 3600
    assert SCHEDULER_JOB_DEFAULTS["coalesce"] is True


async def test_a_job_delayed_by_a_busy_loop_still_runs():
    assert await _job_ran_after_a_blocked_loop(job_defaults=SCHEDULER_JOB_DEFAULTS) is True


async def test_the_apscheduler_default_would_have_dropped_that_job():
    """Контроль: на настройках по умолчанию то же самое срабатывание теряется."""
    assert await _job_ran_after_a_blocked_loop() is False
