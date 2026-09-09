from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.document_request import DocumentRequest
from app.models.password_reset_request import PasswordResetRequest
from app.models.phone_change_request import PhoneChangeRequest
from app.models.project_share_request import ProjectShareRequest
from app.models.registration_request import RegistrationRequest
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
            select(func.count(ProjectShareRequest.id)).where(ProjectShareRequest.status == "pending")
        )).scalar() or 0

        pending_addition = (await self.session.execute(
            select(func.count(CabinetAdditionRequest.id)).where(CabinetAdditionRequest.status == "pending")
        )).scalar() or 0

        pending_phone_change = (await self.session.execute(
            select(func.count(PhoneChangeRequest.id)).where(PhoneChangeRequest.status == "pending")
        )).scalar() or 0

        pending_registration = (await self.session.execute(
            select(func.count(RegistrationRequest.id)).where(RegistrationRequest.status == "pending")
        )).scalar() or 0

        pending_password_reset = (await self.session.execute(
            select(func.count(PasswordResetRequest.id)).where(PasswordResetRequest.status == "pending")
        )).scalar() or 0

        recent = await self._get_recent_activity()

        return DashboardOut(
            stats=DashboardStats(
                unread_chats=unread_chats,
                open_service_requests=open_service,
                pending_document_requests=pending_docs,
                pending_addition_requests=pending_addition,
                pending_project_share_requests=pending_share,
                pending_phone_change_requests=pending_phone_change,
                pending_registration_requests=pending_registration,
                pending_password_reset_requests=pending_password_reset,
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
                cabinet_id=req.cabinet_id, project_id=req.project_id, created_at=req.created_at,
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
            select(ProjectShareRequest, User)
            .outerjoin(User, User.id == ProjectShareRequest.user_id)
            .order_by(ProjectShareRequest.created_at.desc())
            .limit(10)
        )).all()
        for req, user in rows:
            items.append(RecentActivityItem(
                id=req.id, type="share", status=req.status,
                user_id=req.user_id, user_full_name=user.full_name if user else None,
                project_id=req.project_id, created_at=req.created_at,
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

        rows = (await self.session.execute(
            select(PhoneChangeRequest, User)
            .outerjoin(User, User.id == PhoneChangeRequest.user_id)
            .order_by(PhoneChangeRequest.created_at.desc())
            .limit(10)
        )).all()
        for req, user in rows:
            items.append(RecentActivityItem(
                id=req.id, type="phone_change", status=req.status,
                user_id=req.user_id, user_full_name=user.full_name if user else None,
                created_at=req.created_at,
            ))

        rows = (await self.session.execute(
            select(PasswordResetRequest, User)
            .outerjoin(User, User.id == PasswordResetRequest.user_id)
            .order_by(PasswordResetRequest.created_at.desc())
            .limit(10)
        )).all()
        for req, user in rows:
            items.append(RecentActivityItem(
                id=req.id, type="password_reset", status=req.status,
                user_id=req.user_id, user_full_name=user.full_name if user else None,
                created_at=req.created_at,
            ))

        # Заявка на регистрацию — заявитель ещё не пользователь (см.
        # RegistrationRequest), join на users тут не на что делать: user_id
        # нет вообще, полное имя берётся прямо из полей самой заявки
        rows = (await self.session.execute(
            select(RegistrationRequest)
            .order_by(RegistrationRequest.created_at.desc())
            .limit(10)
        )).scalars().all()
        for req in rows:
            items.append(RecentActivityItem(
                id=req.id, type="registration", status=req.status,
                user_id=None, user_full_name=req.full_name,
                created_at=req.created_at,
            ))

        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[:10]
