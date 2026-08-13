from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet_register_override import CabinetRegisterOverride
from app.models.cabinet_telemetry_event import CabinetTelemetryEvent
from app.models.register_definition import RegisterDefinition


class CabinetTelemetryEventRepository:
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

    async def get_by_address(self, address: int) -> RegisterDefinition | None:
        result = await self.session.execute(
            select(RegisterDefinition).where(RegisterDefinition.address == address)
        )
        return result.scalar_one_or_none()

    async def create(self, address: int, name: str, description: str | None) -> RegisterDefinition:
        obj = RegisterDefinition(address=address, name=name, description=description)
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

    async def get_by_cabinet_and_address(
        self, cabinet_id: int, address: int,
    ) -> CabinetRegisterOverride | None:
        result = await self.session.execute(
            select(CabinetRegisterOverride).where(
                CabinetRegisterOverride.cabinet_id == cabinet_id,
                CabinetRegisterOverride.address == address,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, cabinet_id: int, address: int, name: str, description: str | None,
    ) -> CabinetRegisterOverride:
        obj = CabinetRegisterOverride(
            cabinet_id=cabinet_id, address=address, name=name, description=description,
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: CabinetRegisterOverride) -> None:
        await self.session.delete(obj)
        await self.session.flush()
