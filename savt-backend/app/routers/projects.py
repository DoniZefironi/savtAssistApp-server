from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_session
from app.models.user import User
from app.schemas.project import (
    AddProjectByQrIn,
    AddProjectByQrOut,
    UserProjectDetailOut,
    UserProjectListItemOut,
)
from app.services.user_project_service import UserProjectService

router = APIRouter(prefix="/projects", tags=["projects"])

# Все проекты пользователя
@router.get("", response_model=list[UserProjectListItemOut])
async def list_projects(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserProjectService(session)
    return await service.list_projects(current_user.id)

# Подробнее о проекте
@router.get("/{project_id}", response_model=UserProjectDetailOut)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserProjectService(session)
    return await service.get_project(current_user.id, project_id)

# Добавить проект по кур-коду
@router.post("/add-by-qr", response_model=AddProjectByQrOut)
async def add_by_qr(
    payload: AddProjectByQrIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    service = UserProjectService(session)
    result = await service.add_by_qr(user_id=current_user.id, unique_code=payload.parse_unique_code())
    return AddProjectByQrOut(**result)
