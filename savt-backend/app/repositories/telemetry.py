from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_register_override import CabinetRegisterOverride
from app.models.cabinet_register_state import CabinetRegisterState
from app.models.cabinet_telemetry_event import CabinetTelemetryEvent
from app.models.register_definition import RegisterDefinition


class CabinetTelemetryEventRepository:
    """Сырая история сообщений — только для аудита/разбора задним числом (см.
    GET /admin/cabinets/{id}/telemetry/history). "Текущее состояние" для обычной
    ленты — CabinetRegisterStateRepository ниже, не растёт бесконечно."""
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, cabinet_id: int, received_at: datetime, raw_payload: dict,
    ) -> CabinetTelemetryEvent:
        event = CabinetTelemetryEvent(
            cabinet_id=cabinet_id, received_at=received_at, raw_payload=raw_payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_for_cabinet(
        self, cabinet_id: int, offset: int, limit: int,
    ) -> tuple[list[CabinetTelemetryEvent], int]:
        result = await self.session.execute(
            select(CabinetTelemetryEvent)
            .where(CabinetTelemetryEvent.cabinet_id == cabinet_id)
            .order_by(CabinetTelemetryEvent.received_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())
        total = await self.session.scalar(
            select(func.count()).select_from(CabinetTelemetryEvent)
            .where(CabinetTelemetryEvent.cabinet_id == cabinet_id)
        )
        return items, total or 0

    # Автоочистка старой истории (см. schedule в main.py) — не трогает
    # CabinetRegisterState, "текущее состояние" от возраста истории не зависит
    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(CabinetTelemetryEvent)
            .where(CabinetTelemetryEvent.received_at < cutoff)
            .returning(CabinetTelemetryEvent.id)
        )
        return len(result.all())


class CabinetRegisterStateRepository:
    """Текущее состояние — по строке на (cabinet_id, address), перезаписывается
    на каждое новое значение. То, что реально читает GET /cabinets/{id}/telemetry."""
    def __init__(self, session: AsyncSession):
        self.session = session

    # INSERT ... ON CONFLICT (cabinet_id, address) DO UPDATE — один запрос на
    # всю пачку регистров одного сообщения, а не N отдельных UPDATE/INSERT
    async def upsert_many(self, cabinet_id: int, registers: dict[int, int]) -> None:
        if not registers:
            return
        rows = [{"cabinet_id": cabinet_id, "address": address, "value": value} for address, value in registers.items()]
        stmt = pg_insert(CabinetRegisterState).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["cabinet_id", "address"],
            set_={"value": stmt.excluded.value, "updated_at": func.now()},
        )
        await self.session.execute(stmt)

    async def list_for_cabinet(self, cabinet_id: int) -> list[CabinetRegisterState]:
        result = await self.session.execute(
            select(CabinetRegisterState)
            .where(CabinetRegisterState.cabinet_id == cabinet_id)
            .order_by(CabinetRegisterState.address)
        )
        return list(result.scalars().all())


# Стандартная карта регистров — общий словарь, редко больше нескольких десятков
# строк, поэтому без постраничности: расшифровка всегда берёт карту целиком
class RegisterDefinitionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[RegisterDefinition]:
        result = await self.session.execute(
            select(RegisterDefinition).order_by(RegisterDefinition.address)
        )
        return list(result.scalars().all())

    async def get_by_id(self, def_id: int) -> RegisterDefinition | None:
        return await self.session.get(RegisterDefinition, def_id)

    async def get_by_address_and_bit(self, address: int, bit: int) -> RegisterDefinition | None:
        result = await self.session.execute(
            select(RegisterDefinition).where(
                RegisterDefinition.address == address, RegisterDefinition.bit == bit,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, address: int, bit: int, name: str, description: str | None,
    ) -> RegisterDefinition:
        obj = RegisterDefinition(address=address, bit=bit, name=name, description=description)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: RegisterDefinition) -> None:
        await self.session.delete(obj)
        await self.session.flush()


class CabinetRegisterOverrideRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_cabinet(self, cabinet_id: int) -> list[CabinetRegisterOverride]:
        result = await self.session.execute(
            select(CabinetRegisterOverride)
            .where(CabinetRegisterOverride.cabinet_id == cabinet_id)
            .order_by(CabinetRegisterOverride.address)
        )
        return list(result.scalars().all())

    async def get_by_id(self, override_id: int) -> CabinetRegisterOverride | None:
        return await self.session.get(CabinetRegisterOverride, override_id)

    async def get_by_cabinet_address_and_bit(
        self, cabinet_id: int, address: int, bit: int,
    ) -> CabinetRegisterOverride | None:
        result = await self.session.execute(
            select(CabinetRegisterOverride).where(
                CabinetRegisterOverride.cabinet_id == cabinet_id,
                CabinetRegisterOverride.address == address,
                CabinetRegisterOverride.bit == bit,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, cabinet_id: int, address: int, bit: int, name: str, description: str | None,
    ) -> CabinetRegisterOverride:
        obj = CabinetRegisterOverride(
            cabinet_id=cabinet_id, address=address, bit=bit, name=name, description=description,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: CabinetRegisterOverride) -> None:
        await self.session.delete(obj)
        await self.session.flush()
