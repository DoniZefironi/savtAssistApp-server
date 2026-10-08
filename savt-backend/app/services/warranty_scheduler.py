import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.cabinets import Cabinet
from app.models.project import Project
from app.models.user import User
from app.models.user_cabinet import UserCabinet
from app.models.user_project import UserProject
from app.models.warranty_notif_log import WarrantyNotifLog
from app.services.notification_service import NotificationService
from app.utils.db import LOCAL_TZ

logger = logging.getLogger(__name__)

# Пороги предупреждений, дней до конца гарантии: за месяц, за 10 дней и за день
THRESHOLDS = [30, 10, 1]


def _days_label(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        return "день"
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


async def _today(session: AsyncSession) -> date:
    """Сегодняшняя дата по Минску — гарантия считается по местным суткам."""
    return (await session.execute(select(func.date(func.timezone(LOCAL_TZ, func.now()))))).scalar_one()


async def _recipients(session: AsyncSession, cabinet_id: int, project_id: int | None) -> list[int]:
    """Кто видит этот ШУ: участники его проекта (если проект не удалён) и те, кто
    добавил шкаф напрямую по его QR. Заблокированные не получают, как и в
    остальных рассылках; человек с обоими доступами — один раз."""
    sources = [select(UserCabinet.user_id).where(UserCabinet.cabinet_id == cabinet_id)]
    if project_id is not None:
        sources.append(
            select(UserProject.user_id)
            .join(Project, Project.id == UserProject.project_id)
            .where(UserProject.project_id == project_id, Project.deleted_at.is_(None))
        )
    result = await session.execute(
        select(User.id)
        .where(User.id.in_(union(*sources)), User.is_active.is_(True))
        .order_by(User.id)
    )
    return list(result.scalars().all())


async def check_warranty_expiry() -> None:
    """Предупреждает о скором конце гарантии. Шкафу положено одно уведомление на
    порог (30, 10, 1 день), и оно уходит, как только до конца гарантии осталось
    не больше дней, чем порог: в обычный день — ровно в день порога, а если в тот
    день сервер не работал — при следующем запуске, пока гарантия не кончилась.
    В тексте всегда реальное число оставшихся дней.

    Журнал (WarrantyNotifLog) помечает отправленным и сам порог, и все более
    крупные: у только что заведённого шкафа с 8 днями до конца придёт одно
    уведомление про 8 дней, а не три сразу — про 30, 10 и 1."""
    async with AsyncSessionLocal() as session:
        today = await _today(session)
        end_day = func.date(func.timezone(LOCAL_TZ, Cabinet.warranty_ends_at))
        rows = (await session.execute(
            select(Cabinet.id, Cabinet.project_id, Cabinet.admin_internal_name, Cabinet.object_number, end_day)
            .where(
                end_day > today,
                end_day <= today + timedelta(days=max(THRESHOLDS)),
                Cabinet.deleted_at.is_(None),
            )
            .order_by(Cabinet.id)
        )).all()
        if not rows:
            return

        logged: dict[int, set[int]] = defaultdict(set)
        for cabinet_id, days_before in (await session.execute(
            select(WarrantyNotifLog.cabinet_id, WarrantyNotifLog.days_before)
            .where(WarrantyNotifLog.cabinet_id.in_([row[0] for row in rows]))
        )).all():
            logged[cabinet_id].add(days_before)

        for cabinet_id, project_id, admin_name, object_number, ends_on in rows:
            days_left = (ends_on - today).days
            threshold = min(t for t in THRESHOLDS if t >= days_left)
            if threshold in logged[cabinet_id]:
                continue

            # Сразу логируем — если упадём на середине, не будем слать повторно
            for t in THRESHOLDS:
                if t >= threshold and t not in logged[cabinet_id]:
                    session.add(WarrantyNotifLog(cabinet_id=cabinet_id, days_before=t))
            await session.commit()

            user_ids = await _recipients(session, cabinet_id, project_id)
            body = (
                f"Гарантия ШУ «{admin_name or object_number}» истекает через "
                f"{days_left} {_days_label(days_left)}"
            )
            svc = NotificationService(session)
            for user_id in user_ids:
                await svc.send(
                    user_id=user_id,
                    type_="warranty_expiring",
                    title="Гарантия истекает",
                    body=body,
                    data={"cabinet_id": cabinet_id, "days_left": days_left},
                )

            logger.info(
                "Warranty [%dd left, threshold %dd] cabinet_id=%d notified %d user(s)",
                days_left, threshold, cabinet_id, len(user_ids),
            )
