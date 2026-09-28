from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_current_user, get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.pagination import PageOut
from app.schemas.reclamation import (
    AdminReclamationListItemOut,
    AdminReclamationOut,
    AdminReclamationUpdateIn,
    BitrixUserOut,
    ReclamationCreateIn,
    ReclamationDetachedOut,
    ReclamationDetailOut,
    ReclamationListItemOut,
    ReclamationOutboxOut,
    ReclamationOutboxRetryResult,
    ReclamationOutboxUpdateIn,
)
from app.services.reclamation_service import ReclamationService

router = APIRouter(tags=["reclamations"])

# один к одному со стадиями смарт-процесса Bitrix, см. Reclamation.__doc__
_STATUS_PATTERN = "^(new|review|in_progress|resolved|rejected|invalid)$"
_OBJECT_TYPE_PATTERN = "^(cabinet|line|component|software|documentation)$"


# --- Пользователь ---

@router.post("/reclamations", response_model=ReclamationDetailOut, status_code=status.HTTP_201_CREATED)
async def create_reclamation(
    payload: ReclamationCreateIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ReclamationService(session).create(current_user.id, payload)


@router.get("/reclamations", response_model=PageOut[ReclamationListItemOut])
async def list_my_reclamations(
    status: str | None = Query(None, pattern=_STATUS_PATTERN),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ReclamationService(session).list_for_user(current_user.id, status, page, size)


@router.get("/reclamations/{reclamation_id}", response_model=ReclamationDetailOut)
async def get_my_reclamation(
    reclamation_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await ReclamationService(session).get_for_user(current_user.id, reclamation_id)


# --- Администратор ---
# Пока без Битрикса роль "закреплённых специалистов" из ТЗ временно исполняет
# админ вручную (см. app/models/reclamation.py) — в отличие от ServiceRequest,
# у operator здесь нет прав на обработку, только у admin/superadmin.

@router.get("/admin/reclamations", response_model=PageOut[AdminReclamationListItemOut])
async def list_all_reclamations(
    status: str | None = Query(None, pattern=_STATUS_PATTERN),
    object_type: str | None = Query(None, pattern=_OBJECT_TYPE_PATTERN),
    warranty_classification: bool | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ReclamationService(session).list_admin(
        status, object_type, warranty_classification, page, size,
    )


# Статический путь — обязательно до /admin/reclamations/{reclamation_id},
# иначе FastAPI попытается распарсить "bitrix-users" как reclamation_id
@router.get("/admin/reclamations/bitrix-users", response_model=list[BitrixUserOut])
async def list_reclamation_bitrix_users(
    _: User = Depends(require_role(RoleName.ADMIN)),
):
    return await ReclamationService.list_bitrix_users()


# Статический путь — по той же причине, что и bitrix-users выше
@router.get("/admin/reclamations/bitrix-outbox", response_model=list[ReclamationOutboxOut])
async def list_reclamation_bitrix_outbox(
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ReclamationService(session).list_outbox()


# Ручная правка застрявшей операции — переписать payload (например, дописать
# company_id, которого не было в сделке Bitrix на момент сбоя) и сразу
# попробовать отправить, не дожидаясь ближайшего 15-минутного цикла
@router.patch("/admin/reclamations/bitrix-outbox/{outbox_id}", response_model=ReclamationOutboxRetryResult)
async def update_reclamation_bitrix_outbox(
    outbox_id: int,
    payload: ReclamationOutboxUpdateIn,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    result = await ReclamationService(session).retry_outbox_now(outbox_id, payload.payload)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Операция не найдена")
    return result


# Снять операцию с повторов, если чинить не собираемся (например, рекламация
# уже неактуальна) — без этого застрявшая запись висела в очереди навсегда
@router.delete("/admin/reclamations/bitrix-outbox/{outbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reclamation_bitrix_outbox(
    outbox_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    deleted = await ReclamationService(session).delete_outbox(outbox_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Операция не найдена")


# Статический путь — по той же причине, что и bitrix-users выше
@router.get("/admin/reclamations/bitrix-detached", response_model=list[ReclamationDetachedOut])
async def list_reclamations_detached_from_bitrix(
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ReclamationService(session).list_detached()


@router.get("/admin/reclamations/{reclamation_id}", response_model=AdminReclamationOut)
async def get_reclamation_admin(
    reclamation_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ReclamationService(session).get_admin(reclamation_id)


@router.patch("/admin/reclamations/{reclamation_id}", response_model=AdminReclamationOut)
async def update_reclamation(
    reclamation_id: int,
    payload: AdminReclamationUpdateIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    changed = payload.model_dump(exclude_unset=True)
    return await ReclamationService(session).update(reclamation_id, changed, actor.id, actor_role)


# Только для рекламаций из GET /admin/reclamations/bitrix-detached — живую
# удалить нельзя (400), см. ReclamationService.delete_detached
@router.delete("/admin/reclamations/{reclamation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reclamation(
    reclamation_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await ReclamationService(session).delete_detached(reclamation_id, actor.id, actor_role)
