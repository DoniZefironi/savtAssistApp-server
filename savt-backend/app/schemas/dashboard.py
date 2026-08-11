from datetime import datetime

from pydantic import BaseModel


class DashboardStats(BaseModel):
    unread_chats: int
    open_service_requests: int
    pending_document_requests: int
    pending_addition_requests: int
    # Раньше здесь считались заявки на доступ к конкретному ШУ
    # (CabinetShareRequest) — доступ теперь только через проект, см.
    # ProjectShareRequest (заявка на вступление в проект целиком)
    pending_project_share_requests: int


class RecentActivityItem(BaseModel):
    id: int
    type: str
    status: str
    user_id: int
    user_full_name: str | None
    cabinet_id: int | None = None
    project_id: int | None = None
    created_at: datetime


class DashboardOut(BaseModel):
    stats: DashboardStats
    recent_activity: list[RecentActivityItem]
