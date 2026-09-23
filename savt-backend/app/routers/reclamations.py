from fastapi import APIRouter, Depends, Query, status
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
    ReclamationDetailOut,
    ReclamationListItemOut,
    ReclamationOutboxOut,
)
from app.services.reclamation_service import ReclamationService

router = APIRouter(tags=["reclamations"])

_STATUS_PATTERN = "^(review|in_progress|resolved|rejected)$"
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
