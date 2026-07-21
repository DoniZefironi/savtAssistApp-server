from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.pagination import PageOut
from app.schemas.project import ProjectCreateIn, ProjectListOut, ProjectOut, ProjectUpdateIn
from app.services.project_service import ProjectService

router = APIRouter(prefix="/admin/projects", tags=["admin: projects"])

# Создать проект
@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).create(payload, actor.id, actor_role)

# Все проекты
@router.get("", response_model=PageOut[ProjectListOut])
async def list_projects(
    search: str | None = Query(None),
    sort_by: str = Query("created_at", pattern="^(name|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).list_all(
        query=search, sort_by=sort_by, sort_order=sort_order, page=page, size=size,
    )

# Подробнее о проекте
@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).get(project_id)

# Обновить проект
@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    payload: ProjectUpdateIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).update(project_id, payload, actor.id, actor_role)

# Удалить проект
@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    await ProjectService(session).delete(project_id, actor.id, actor_role)
