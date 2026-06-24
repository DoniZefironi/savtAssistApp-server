from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.user import User
from app.schemas.dashboard import DashboardOut
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    operator: User = Depends(require_role(RoleName.OPERATOR, RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await DashboardService(session).get_dashboard(operator.id)
