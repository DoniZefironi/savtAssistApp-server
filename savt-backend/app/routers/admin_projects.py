from datetime import date

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
    SyncAllFoldersResultOut,
    SyncFolderResultOut,
)
from app.services import project_code_service
from app.services.project_service import ProjectService

router = APIRouter(prefix="/admin/projects", tags=["admin: projects"])

# Синхронизировать папку проекта на NAS прямо сейчас — то же, что делает ночной
# прогон, но по кнопке. Нужно, когда файл положили в папку напрямую и ждать
# ночи не хочется. Ограничение по гарантии здесь не применяется: раз админ
# нажал кнопку осознанно, синхронизируем даже просроченный проект.
@router.post("/{project_id}/sync-folder", response_model=SyncFolderResultOut)
async def sync_project_folder_now(
    project_id: int,
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).sync_folder_now(project_id)


# Синхронизировать папки ВСЕХ проектов на NAS прямо сейчас, одним запросом —
# то же самое, что ночной прогон (см. sync_all_project_folders), но по кнопке,
# без ожидания до 2:00 и без похода в каждый проект отдельно. Ограничение по
# гарантии здесь, в отличие от ручки одного проекта, сохраняется.
@router.post("/sync-folders", response_model=SyncAllFoldersResultOut)
async def sync_all_project_folders_now(
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).sync_all_folders_now()


# Создать проект
@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateIn,
    actor: User = Depends(require_role(RoleName.ADMIN)),
    actor_role: str = Depends(get_role_from_token),
    session: AsyncSession = Depends(get_session),
):
    return await ProjectService(session).create(payload, actor.id, actor_role)

# Все проекты. Фильтры двух видов: has_* / tag_ids / cabinet_warranty_status
# отбирают по шкафам проекта (подошёл хотя бы один), остальные — по самому
# проекту. Заданные вместе складываются по И.
@router.get("", response_model=PageOut[ProjectListOut])
async def list_projects(
    search: str | None = Query(None),
    # по шкафам проекта
    tag_ids: list[int] = Query(default=[]),
    has_documents: bool | None = Query(None),
    has_photos: bool | None = Query(None),
    has_users: bool | None = Query(None),
    has_service_requests: bool | None = Query(None),
    cabinet_warranty_status: str | None = Query(None, pattern="^(active|expiring_soon|expired|none)$"),
    # по самому проекту
    year: int | None = Query(None, ge=2000, le=2100),
    company: str | None = Query(None),
    shipped: bool | None = Query(None),
    # Календарные даты, YYYY-MM-DD. Обе границы включающие: "по 15 августа"
    # забирает весь день. datetime здесь нельзя — без зоны PostgreSQL истолковал
    # бы его в часовом поясе сессии и граница уехала бы на несколько часов
    shipment_planned_from: date | None = Query(None),
    shipment_planned_to: date | None = Query(None),
    shipment_actual_from: date | None = Query(None),
    shipment_actual_to: date | None = Query(None),
    has_project_documents: bool | None = Query(None),
    has_project_photos: bool | None = Query(None),
    has_project_users: bool | None = Query(None),
    has_contacts: bool | None = Query(None),
    warranty_status: str | None = Query(None, pattern="^(active|expiring_soon|expired|none)$"),
    sort_by: str = Query(
        "created_at",
        pattern="^(name|created_at|production_number|year|company_name"
                "|shipment_planned_at|shipment_actual_at|warranty_ends_at|cabinet_count)$",
    ),
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
        cabinet_warranty_status=cabinet_warranty_status,
        year=year, company=company, shipped=shipped,
        shipment_planned_from=shipment_planned_from,
        shipment_planned_to=shipment_planned_to,
        shipment_actual_from=shipment_actual_from,
        shipment_actual_to=shipment_actual_to,
        has_project_documents=has_project_documents,
        has_project_photos=has_project_photos,
        has_project_users=has_project_users,
        has_contacts=has_contacts,
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
