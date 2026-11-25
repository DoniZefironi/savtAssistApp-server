from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session, require_telephony_secret
from app.schemas.telephony import ResolveObjectIn, ResolveObjectOut
from app.services.telephony_service import resolve_object


router = APIRouter(
    prefix="/internal/telephony",
    tags=["internal: telephony"],
    dependencies=[Depends(require_telephony_secret)],
)


@router.post("/resolve-object", response_model=ResolveObjectOut)
async def resolve_telephony_object(
    payload: ResolveObjectIn,
    session: AsyncSession = Depends(get_session),
) -> ResolveObjectOut:
    return await resolve_object(session, payload)
