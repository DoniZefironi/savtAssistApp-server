from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.role import Role
from app.models.user import User
from app.services import bitrix_staff_sync

router = APIRouter(prefix="/admin/staff", tags=["admin: staff"])


# Сотрудники Bitrix -> операторы/админы/суперадмины. Раз в час то же самое
# делает фоновая задача; здесь — запустить сразу (например, после приёма на
# работу) и увидеть отчёт: кого завели, кого деактивировали, кого и почему
# пропустили (нет телефона, повторяющийся номер, номер занят)
@router.post("/bitrix-sync")
async def sync_staff_from_bitrix(
    current_user: User = Depends(require_role(RoleName.SUPERADMIN)),
    session: AsyncSession = Depends(get_session),
):
    role = await session.get(Role, current_user.role_id)
    report = await bitrix_staff_sync.run_sync(session, current_user.id, role.name if role else None)
    if report is None:
        raise HTTPException(status_code=502, detail="Bitrix недоступен или не настроен")
    return {"counts": report.counts(), **report.as_dict()}
