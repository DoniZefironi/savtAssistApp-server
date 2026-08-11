import logging
from datetime import date, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.cabinets import Cabinet
from app.models.user_project import UserProject
from app.models.warranty_notif_log import WarrantyNotifLog
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

THRESHOLDS = [30, 10, 1]


def _days_label(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        return "день"
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


async def check_warranty_expiry() -> None:
    async with AsyncSessionLocal() as session:
        today = date.today()
        for days in THRESHOLDS:
            target = today + timedelta(days=days)
            await _process_threshold(session, target, days)


async def _process_threshold(
    session: AsyncSession, target_date: date, days_before: int
) -> None:
    # Шкафы, у которых гарантия заканчивается target_date и уведомление ещё не отправлялось
    already_notified = select(WarrantyNotifLog.cabinet_id).where(
        WarrantyNotifLog.days_before == days_before
    )
    stmt = select(Cabinet).where(
        func.date(Cabinet.warranty_ends_at) == target_date,
        Cabinet.id.not_in(already_notified),
        Cabinet.deleted_at.is_(None),
    )
    cabinets = (await session.execute(stmt)).scalars().all()

    for cabinet in cabinets:
        # Сразу логируем — если упадём на середине, не будем слать повторно
        session.add(WarrantyNotifLog(cabinet_id=cabinet.id, days_before=days_before))
        await session.commit()

        # Доступ выводится из проекта — уведомляем всех участников проекта,
        # которому принадлежит шкаф (у ШУ без проекта уведомлять некого)
        user_ids = [] if cabinet.project_id is None else (
            await session.execute(
                select(UserProject.user_id).where(UserProject.project_id == cabinet.project_id)
            )
        ).scalars().all()

        name = cabinet.admin_internal_name or cabinet.object_number
        title = "Гарантия истекает"
        body = f"Гарантия ШУ «{name}» истекает через {days_before} {_days_label(days_before)}"

        svc = NotificationService(session)
        for user_id in user_ids:
            await svc.send(
                user_id=user_id,
                type_="warranty_expiring",
                title=title,
                body=body,
                data={"cabinet_id": cabinet.id, "days_left": days_before},
            )

        logger.info(
            "Warranty [%dd] cabinet_id=%d notified %d user(s)",
            days_before, cabinet.id, len(user_ids),
        )
