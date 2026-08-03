from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_role_from_token, get_session, require_role
from app.models.user import User
from app.schemas.pagination import PageOut
from app.schemas.project import (
    DecodeProjectCodeIn,
    DecodeProjectCodeOut,
    ProjectCreateIn,
    ProjectListOut,
    ProjectOut,
    ProjectUpdateIn,
)
from app.services import project_code_service
from app.services.project_service import ProjectService

router = APIRouter(prefix="/admin/projects", tags=["admin: projects"])

# Синхронизировать папку проекта на NAS прямо сейчас — то же, что делает ночной
# прогон, но по кнопке. Нужно, когда файл положили в папку напрямую и ждать
# ночи не хочется. Ограничение по гарантии здесь не применяется: раз админ
# нажал кнопку осознанно, синхронизируем даже просроченный проект.
@router.post("/{project_id}/sync-folder")
async def sync_project_folder_now(
    project_id: int,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).sync_folder_now(project_id)


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
    tag_ids: list[int] = Query(default=[]),
    has_documents: bool | None = Query(None),
    has_photos: bool | None = Query(None),
    has_users: bool | None = Query(None),
    has_service_requests: bool | None = Query(None),
    warranty_status: str | None = Query(None, pattern="^(active|expiring_soon|expired|none)$"),
    sort_by: str = Query("created_at", pattern="^(name|created_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).list_all(
        query=search, tag_ids=tag_ids or None,
        has_documents=has_documents, has_photos=has_photos,
        has_users=has_users, has_service_requests=has_service_requests,
        warranty_status=warranty_status,
        sort_by=sort_by, sort_order=sort_order, page=page, size=size,
    )

# Расшифровать unique_code обратно в номер проекта из Bitrix (например "26_138").
# Регистрируется ДО "/{project_id}" — иначе "decode-code" словится как project_id и упадёт в 422.
@router.post("/decode-code", response_model=DecodeProjectCodeOut)
async def decode_project_code(
    payload: DecodeProjectCodeIn,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
):
    production_number = project_code_service.decrypt_project_code(payload.code)
    if production_number is None:
        raise HTTPException(status_code=400, detail="Код не удалось расшифровать — неверный или повреждён")
    return DecodeProjectCodeOut(production_number=production_number)

# Подробнее о проекте (cabinets в ответе отфильтрован теми же параметрами)
@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    tag_ids: list[int] = Query(default=[]),
    has_documents: bool | None = Query(None),
    has_photos: bool | None = Query(None),
    has_users: bool | None = Query(None),
    has_service_requests: bool | None = Query(None),
    warranty_status: str | None = Query(None, pattern="^(active|expiring_soon|expired|none)$"),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).get(
        project_id, tag_ids=tag_ids or None,
        has_documents=has_documents, has_photos=has_photos,
        has_users=has_users, has_service_requests=has_service_requests,
        warranty_status=warranty_status,
    )

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

# Ручной запуск синхронизации папки проекта на NAS (вне расписания и вне
# проверки актуальности гарантии — явное действие админа срабатывает всегда)
@router.post("/{project_id}/sync-folder", response_model=ProjectOut)
async def sync_project_folder(
    project_id: int,
    _: User = Depends(require_role(RoleName.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).sync_folder(project_id)
