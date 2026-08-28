import asyncio

from fastapi import APIRouter, Depends, Query
from app.config import settings
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
        sim_url=settings.sim_service_frontend_url,
    )


# Общий поисковый текст сразу по нескольким полям — фронт пока не даёт выбрать
# конкретное поле, любой текст (номер, имя, серийник) уходит в один параметр
# name. У SimApi же search — это отдельные И-фильтры (см. SearchSimDto), не
# "искать по любому полю", поэтому опрашиваем name/phone/serialNumber
# параллельно и объединяем по id, без дублей.
#
# limit=100 на каждое поле — если у одного поля совпадений больше сотни,
# лишние результаты не попадут; для нынешнего объёма SIM (сотни, не тысячи)
# этого достаточно, но при росте базы стоит будет пересмотреть.
async def _search_across_fields(query: str) -> list[dict]:
    results = await asyncio.gather(
        sim_service.search_sims(page=1, limit=100, name=query),
        sim_service.search_sims(page=1, limit=100, phone=query),
        sim_service.search_sims(page=1, limit=100, serial_number=query),
    )
    seen: set[str] = set()
    merged: list[dict] = []
    for items, _total in results:
        for item in items:
            item_id = item.get("id")
            if item_id is not None and item_id not in seen:
                seen.add(item_id)
                merged.append(item)
    return merged


# Поиск/список SIM во внешнем приложении (10.1.0.67:5000) — для выбора SIM
# при привязке к ШУ через PATCH /admin/cabinets/{id} (поле sim_id). Без
# фильтров — обычный постраничный список. Если задан только name (обычный
# случай сейчас, см. _search_across_fields) — общий поиск сразу по нескольким
# полям. Если явно заданы phone/serial_number/ip — узкий поиск ровно по ним
# (один запрос к SimApi, обычная пагинация SimApi, см. sim_service.search_sims).
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
    if phone or serial_number or ip:
        items, total = await sim_service.search_sims(
            page=page, limit=size, name=name, phone=phone, serial_number=serial_number, ip=ip,
        )
        return make_page([_to_sim_info(item) for item in items], total, page, size)

    if name:
        merged = await _search_across_fields(name)
        start = (page - 1) * size
        page_items = merged[start:start + size]
        return make_page([_to_sim_info(item) for item in page_items], len(merged), page, size)

    items, total = await sim_service.list_sims(page=page, limit=size)
    return make_page([_to_sim_info(item) for item in items], total, page, size)
