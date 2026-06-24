from datetime import datetime

from pydantic import BaseModel


class DashboardStats(BaseModel):
    unread_chats: int
    open_service_requests: int
    pending_document_requests: int
    pending_share_requests: int
    pending_addition_requests: int


class RecentActivityItem(BaseModel):
    id: int
    type: str
    status: str
    user_id: int
    user_full_name: str | None
    cabinet_id: int | None
    created_at: datetime


class DashboardOut(BaseModel):
    stats: DashboardStats
    recent_activity: list[RecentActivityItem]
