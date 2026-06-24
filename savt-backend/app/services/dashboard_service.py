from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.cabinet_share_request import CabinetShareRequest
from app.models.document_request import DocumentRequest
from app.models.service_request import ServiceRequest
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.schemas.dashboard import DashboardOut, DashboardStats, RecentActivityItem


class DashboardService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_dashboard(self, operator_id: int) -> DashboardOut:
        unread_chats = await ChatRepository(self.session).count_unread_chats(operator_id)

        open_service = (await self.session.execute(
            select(func.count(ServiceRequest.id)).where(ServiceRequest.status == "open")
        )).scalar() or 0

        pending_docs = (await self.session.execute(
            select(func.count(DocumentRequest.id)).where(DocumentRequest.status == "pending")
        )).scalar() or 0

        pending_share = (await self.session.execute(
            select(func.count(CabinetShareRequest.id)).where(CabinetShareRequest.status == "pending")
        )).scalar() or 0

        pending_addition = (await self.session.execute(
            select(func.count(CabinetAdditionRequest.id)).where(CabinetAdditionRequest.status == "pending")
        )).scalar() or 0

        recent = await self._get_recent_activity()

        return DashboardOut(
            stats=DashboardStats(
                unread_chats=unread_chats,
                open_service_requests=open_service,
                pending_document_requests=pending_docs,
                pending_share_requests=pending_share,
                pending_addition_requests=pending_addition,
            ),
            recent_activity=recent,
        )

    async def _get_recent_activity(self) -> list[RecentActivityItem]:
        items: list[RecentActivityItem] = []

        rows = (await self.session.execute(
            select(ServiceRequest, User)
            .outerjoin(User, User.id == ServiceRequest.user_id)
            .order_by(ServiceRequest.created_at.desc())
            .limit(10)
        )).all()
        for req, user in rows:
            items.append(RecentActivityItem(
                id=req.id, type="service", status=req.status,
                user_id=req.user_id, user_full_name=user.full_name if user else None,
                cabinet_id=req.cabinet_id, created_at=req.created_at,
            ))

        rows = (await self.session.execute(
            select(DocumentRequest, User)
            .outerjoin(User, User.id == DocumentRequest.user_id)
            .order_by(DocumentRequest.created_at.desc())
            .limit(10)
        )).all()
        for req, user in rows:
            items.append(RecentActivityItem(
                id=req.id, type="document", status=req.status,
                user_id=req.user_id, user_full_name=user.full_name if user else None,
                cabinet_id=req.cabinet_id, created_at=req.created_at,
            ))

        rows = (await self.session.execute(
            select(CabinetShareRequest, User)
            .outerjoin(User, User.id == CabinetShareRequest.user_id)
            .order_by(CabinetShareRequest.created_at.desc())
            .limit(10)
        )).all()
        for req, user in rows:
            items.append(RecentActivityItem(
                id=req.id, type="share", status=req.status,
                user_id=req.user_id, user_full_name=user.full_name if user else None,
                cabinet_id=req.cabinet_id, created_at=req.created_at,
            ))

        rows = (await self.session.execute(
            select(CabinetAdditionRequest, User)
            .outerjoin(User, User.id == CabinetAdditionRequest.user_id)
            .order_by(CabinetAdditionRequest.created_at.desc())
            .limit(10)
        )).all()
        for req, user in rows:
            items.append(RecentActivityItem(
                id=req.id, type="addition", status=req.status,
                user_id=req.user_id, user_full_name=user.full_name if user else None,
                cabinet_id=req.cabinet_id, created_at=req.created_at,
            ))

        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[:10]
