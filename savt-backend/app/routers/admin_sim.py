from fastapi import APIRouter, Depends, Query
from app.core.constants import RoleName
from app.core.dependencies import require_role
from app.models.user import User
from app.schemas.cabinet import SimInfoOut
from app.schemas.pagination import PageOut, make_page
from app.services import sim_service

router = APIRouter(prefix="/admin/sim", tags=["admin: sim"])


def _to_sim_info(raw: dict) -> SimInfoOut:
    return SimInfoOut(
        id=raw["id"],
        serial_number=raw.get("serialNumber"),
        phone=raw.get("phone"),
        ip=raw.get("ip"),
    )


# Поиск/список SIM во внешнем приложении (10.1.0.67:5000) — для выбора SIM
# при привязке к ШУ через PATCH /admin/cabinets/{id} (поле sim_id). Без
# фильтров — обычный постраничный список; с любым из name/phone/serial_number/ip —
# поиск по подстроке (см. sim_service.search_sims).
@router.get("", response_model=PageOut[SimInfoOut])
async def search_sim(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    name: str | None = Query(None),
    phone: str | None = Query(None),
    serial_number: str | None = Query(None),
    ip: str | None = Query(None),
    _: User = Depends(require_role(RoleName.ADMIN, RoleName.OPERATOR)),
):
    if any((name, phone, serial_number, ip)):
        items, total = await sim_service.search_sims(
            page=page, limit=size, name=name, phone=phone, serial_number=serial_number, ip=ip,
        )
    else:
        items, total = await sim_service.list_sims(page=page, limit=size)
    return make_page([_to_sim_info(item) for item in items], total, page, size)
