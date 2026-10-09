from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_addition_request import CabinetAdditionRequest
from app.models.document_request import DocumentRequest
from app.models.password_reset_request import PasswordResetRequest
from app.models.phone_change_request import PhoneChangeRequest
from app.models.reclamation import Reclamation
from app.models.registration_request import RegistrationRequest
from app.models.service_request import ServiceRequest
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.schemas.dashboard import DashboardOut, DashboardStats, RecentActivityItem


def _snippet(text: str | None, limit: int = 80) -> str | None:
    if not text:
        return None
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


_RECENT_PER_SOURCE = 10

# Заявки, которые ждут решения сотрудника: счётчик дашборда — число записей в
# статусе "pending". Поле статистики → модель.
_PENDING_REQUESTS = {
    "pending_document_requests": DocumentRequest,
    "pending_addition_requests": CabinetAdditionRequest,
    "pending_phone_change_requests": PhoneChangeRequest,
    "pending_registration_requests": RegistrationRequest,
    "pending_password_reset_requests": PasswordResetRequest,
}

# Источники ленты, у которых есть автор-пользователь: (тип в ленте, модель,
# что показать в строке события). Подпись зависит от вида заявки.
_USER_ACTIVITY_SOURCES = (
    ("service", ServiceRequest, lambda r: dict(
        cabinet_id=r.cabinet_id, project_id=r.project_id, detail=_snippet(r.description))),
    ("document", DocumentRequest, lambda r: dict(cabinet_id=r.cabinet_id, detail=r.doc_type)),
    ("addition", CabinetAdditionRequest, lambda r: dict(
        cabinet_id=r.cabinet_id, detail=_snippet(r.user_comment))),
    ("phone_change", PhoneChangeRequest, lambda r: dict(detail=r.new_phone)),
    ("password_reset", PasswordResetRequest, lambda r: dict(detail=_snippet(r.user_comment))),
    ("reclamation", Reclamation, lambda r: dict(
        cabinet_id=r.cabinet_id, detail=_snippet(r.description))),
)


class DashboardService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _count(self, model, *conditions) -> int:
        return (await self.session.execute(
            select(func.count(model.id)).where(*conditions)
        )).scalar() or 0

    async def get_dashboard(self, operator_id: int) -> DashboardOut:
        pending = {
            field: await self._count(model, model.status == "pending")
            for field, model in _PENDING_REQUESTS.items()
        }
        return DashboardOut(
            stats=DashboardStats(
                unread_chats=await ChatRepository(self.session).count_unread_chats(operator_id),
                open_service_requests=await self._count(ServiceRequest, ServiceRequest.status == "open"),
                # "ещё не взяли в работу" — это обе ранние стадии смарт-процесса:
                # new ("Новая рекламация") и review ("На рассмотрении")
                pending_reclamations=await self._count(Reclamation, Reclamation.status.in_(("new", "review"))),
                **pending,
            ),
            recent_activity=await self._get_recent_activity(),
        )

    async def _get_recent_activity(self) -> list[RecentActivityItem]:
        items: list[RecentActivityItem] = []

        for activity_type, model, details in _USER_ACTIVITY_SOURCES:
            rows = (await self.session.execute(
                select(model, User)
                .outerjoin(User, User.id == model.user_id)
                .order_by(model.created_at.desc())
                .limit(_RECENT_PER_SOURCE)
            )).all()
            for req, user in rows:
                items.append(RecentActivityItem(
                    id=req.id, type=activity_type, status=req.status,
                    user_id=req.user_id, user_full_name=user.full_name if user else None,
                    created_at=req.created_at, **details(req),
                ))

        # Заявка на регистрацию — заявитель ещё не пользователь (см.
        # RegistrationRequest), join на users тут не на что делать: user_id
        # нет вообще, полное имя берётся прямо из полей самой заявки
        rows = (await self.session.execute(
            select(RegistrationRequest)
            .order_by(RegistrationRequest.created_at.desc())
            .limit(_RECENT_PER_SOURCE)
        )).scalars().all()
        for req in rows:
            items.append(RecentActivityItem(
                id=req.id, type="registration", status=req.status,
                user_id=None, user_full_name=req.full_name,
                detail=req.organization_name or req.phone, created_at=req.created_at,
            ))

        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[:10]
