import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session, require_telephony_secret
from app.schemas.telephony import AnswerQuestionIn, AnswerQuestionOut, ResolveObjectIn, ResolveObjectOut
from app.services.telephony_service import answer_question, resolve_object


_log = logging.getLogger(__name__)
_ANSWER_TIMEOUT_SECONDS = 8


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


@router.post("/answer-question", response_model=AnswerQuestionOut)
async def answer_telephony_question(
    payload: AnswerQuestionIn,
    session: AsyncSession = Depends(get_session),
) -> AnswerQuestionOut:
    try:
        async with asyncio.timeout(_ANSWER_TIMEOUT_SECONDS):
            return await answer_question(session, payload.question)
    except Exception as exc:
        _log.warning("Ответ для телефонии недоступен (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Не удалось ответить на вопрос") from None
