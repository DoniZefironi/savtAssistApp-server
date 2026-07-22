from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.cabinet import CabinetCreateIn, CabinetGeoItem, CabinetListOut, CabinetOut, CabinetUpdateIn
from app.schemas.pagination import PageOut
from app.schemas.project import CabinetProjectPatchIn
from app.schemas.tags import DocumentTagsIn
from app.services.cabinet_service import CabinetService
from app.services.project_service import ProjectService

router = APIRouter(prefix="/admin/cabinets", tags=["admin: cabinets"])

# Создать ШУ
@router.post("", response_model=CabinetOut, status_code=status.HTTP_201_CREATED)
async def create_cabinet(
    payload: CabinetCreateIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).create(payload, actor.id, actor_role)

# Все ШУ
@router.get("", response_model=PageOut[CabinetListOut])
async def list_cabinets(
    search: str | None = Query(None),
    tag_ids: list[int] = Query(default=[]),
    has_documents: bool | None = Query(None),
    has_photos: bool | None = Query(None),
    has_users: bool | None = Query(None),
    has_service_requests: bool | None = Query(None),
    warranty_status: str | None = Query(None, pattern="^(active|expired|none)$"),
    has_project: bool | None = Query(None),
    project_id: int | None = Query(None, gt=0),
    sort_by: str = Query("created_at", pattern="^(type|warranty_ends_at|object_number|admin_internal_name|purpose|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).list_all(
        query=search, tag_ids=tag_ids or None,
        has_documents=has_documents, has_photos=has_photos,
        has_users=has_users, has_service_requests=has_service_requests,
        warranty_status=warranty_status, has_project=has_project, project_id=project_id,
        sort_by=sort_by, sort_order=sort_order, page=page, size=size,
    )

# Гео-данные всех ШУ — ДОЛЖЕН быть ДО /{cabinet_id}
@router.get("/geo", response_model=list[CabinetGeoItem])
async def get_cabinets_geo(
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).get_geo()


# Подробнее о ШУ
@router.get("/{cabinet_id}", response_model=CabinetOut)
async def get_cabinet(
    cabinet_id: int,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).get(cabinet_id)

# Обновить инфу о ШУ
@router.patch("/{cabinet_id}", response_model=CabinetOut)
async def update_cabinet(
    cabinet_id: int,
    payload: CabinetUpdateIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await CabinetService(session).update(cabinet_id, payload, actor.id, actor_role)

# Удалить ШУ
@router.delete("/{cabinet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cabinet(
    cabinet_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await CabinetService(session).delete(cabinet_id, actor.id, actor_role)


# Привязать теги к ШУ (полная замена)
@router.put("/{cabinet_id}/tags", status_code=status.HTTP_204_NO_CONTENT)
async def set_cabinet_tags(
    cabinet_id: int,
    payload: DocumentTagsIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await CabinetService(session).set_tags(cabinet_id, payload.tag_ids, actor.id, actor_role)

# Привязать/отвязать ШУ к проекту (project_id: null — отвязать)
@router.patch("/{cabinet_id}/project", status_code=status.HTTP_204_NO_CONTENT)
async def set_cabinet_project(
    cabinet_id: int,
    payload: CabinetProjectPatchIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await ProjectService(session).set_cabinet_project(cabinet_id, payload, actor.id, actor_role)
