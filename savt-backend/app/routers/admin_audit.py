from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.dependencies import get_session, require_role
from app.models.user import User
from app.schemas.audit import AuditLogOut
from app.schemas.pagination import PageOut
from app.services.audit_service import AuditService

router = APIRouter(prefix="/admin/audit-logs", tags=["admin: audit"])

_ROLES = "^(admin|operator|user|system)$"
_SORT = "^(created_at|action|entity_type|actor_role|actor_id)$"
_SEARCH_IN = "^(all|action|entity_type|actor_name|payload)$"


@router.get("", response_model=PageOut[AuditLogOut])
async def list_audit_logs(
    # фильтры
    actor_id: int | None = Query(None, gt=0),
    actor_role: str | None = Query(None, pattern=_ROLES),
    action: str | None = Query(None, max_length=100),
    entity_type: str | None = Query(None, max_length=50),
    entity_id: int | None = Query(None, gt=0),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    # поиск
    search: str | None = Query(None, min_length=1, max_length=200),
    search_in: str = Query("all", pattern=_SEARCH_IN),
    # сортировка
    sort_by: str = Query("created_at", pattern=_SORT),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    # пагинация
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
    session: AsyncSession = Depends(get_session),
) -> PageOut[AuditLogOut]:
    return await AuditService(session).list_logs(
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        search=search,
        search_in=search_in,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        size=size,
    )
