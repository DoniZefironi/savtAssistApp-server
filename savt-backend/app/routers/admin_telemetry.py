from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.pagination import PageOut
from app.schemas.telemetry import (
    CabinetRegisterOverrideIn,
    CabinetRegisterOverrideOut,
    RegisterDefinitionIn,
    RegisterDefinitionOut,
    TelemetryCurrentStateOut,
    TelemetryEventOut,
)
from app.services.telemetry_service import AdminRegisterMapService, UserTelemetryService

router = APIRouter(prefix="/admin", tags=["admin: telemetry"])


# Текущее состояние регистров ШУ для админки/операторской панели — в отличие от
# GET /cabinets/{id}/telemetry (мобильное приложение), доступ не завязан на
# членство в проекте: оператор/админ должен видеть телеметрию любого ШУ
@router.get("/cabinets/{cabinet_id}/telemetry", response_model=TelemetryCurrentStateOut)
async def get_cabinet_telemetry_admin(
    cabinet_id: int,
    include_unnamed: bool = Query(
        False, description="Показывать и регистры без названия — для настройки карты",
    ),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await UserTelemetryService(session).get_current_state_admin(cabinet_id, include_unnamed)


# Сырая история сообщений — для аудита/разбора задним числом ("когда началась
# авария"). Хранится ограниченное время (см. TELEMETRY_HISTORY_RETENTION_DAYS),
# в отличие от текущего состояния выше, которое не история и не чистится по возрасту
@router.get("/cabinets/{cabinet_id}/telemetry/history", response_model=PageOut[TelemetryEventOut])
async def get_cabinet_telemetry_history(
    cabinet_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    include_unnamed: bool = Query(
        False, description="Показывать и регистры без названия — для настройки карты",
    ),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await UserTelemetryService(session).list_history_for_cabinet_admin(
        cabinet_id, page, size, include_unnamed,
    )


# Стандартная карта регистров — общая для всех ШУ
@router.get("/register-definitions", response_model=list[RegisterDefinitionOut])
async def list_register_definitions(
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminRegisterMapService(session).list_definitions()


@router.post("/register-definitions", response_model=RegisterDefinitionOut, status_code=status.HTTP_201_CREATED)
async def create_register_definition(
    payload: RegisterDefinitionIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await AdminRegisterMapService(session).create_definition(
        payload.address, payload.bit, payload.name, payload.description, actor.id, actor_role,
    )


@router.delete("/register-definitions/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_register_definition(
    definition_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminRegisterMapService(session).delete_definition(definition_id, actor.id, actor_role)


# Добавки/переопределения карты для конкретного ШУ
@router.get("/cabinets/{cabinet_id}/register-overrides", response_model=list[CabinetRegisterOverrideOut])
async def list_cabinet_register_overrides(
    cabinet_id: int,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await AdminRegisterMapService(session).list_overrides(cabinet_id)


@router.post(
    "/cabinets/{cabinet_id}/register-overrides",
    response_model=CabinetRegisterOverrideOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_cabinet_register_override(
    cabinet_id: int,
    payload: CabinetRegisterOverrideIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await AdminRegisterMapService(session).create_override(
        cabinet_id, payload.address, payload.bit, payload.name, payload.description, actor.id, actor_role,
    )


@router.delete(
    "/cabinets/{cabinet_id}/register-overrides/{override_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_cabinet_register_override(
    cabinet_id: int,
    override_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await AdminRegisterMapService(session).delete_override(cabinet_id, override_id, actor.id, actor_role)
