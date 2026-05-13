import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.cabinets import Cabinet
from app.repositories.cabinet import CabinetRepository
from app.schemas.cabinet import CabinetCreateIn, CabinetUpdateIn


class CabinetService:
    def __init__(self, session: AsyncSession):
        self.repo = CabinetRepository(session)
        self.session = session

    # Создание ШУ
    async def create(self, data: CabinetCreateIn) -> Cabinet:
        unique_code = await self._generate_unique_code()
        cabinet = await self.repo.create(
            unique_code=unique_code,
            type=data.type,
            object_number=data.object_number,
            description=data.description,
            warranty_starts_at=data.warranty_starts_at,
            warranty_ends_at=data.warranty_ends_at,
            admin_internal_name=data.admin_internal_name,
            admin_comment=data.admin_comment,
            purpose=data.purpose,
        )
        await self.session.commit()
        await self.session.refresh(cabinet)
        return cabinet

    # Получение ШУ
    async def get(self, cabinet_id: int) -> Cabinet:
        cabinet = await self.repo.get_by_id(cabinet_id)
        if cabinet is None:
            raise NotFoundError("ШУ не найден")
        return cabinet

    # Обновление ШУ
    async def update(self, cabinet_id: int, data: CabinetUpdateIn) -> Cabinet:
        cabinet = await self.get(cabinet_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(cabinet, field, value)
        await self.session.commit()
        await self.session.refresh(cabinet)
        return cabinet

    # Удаление ШУ
    async def delete(self, cabinet_id: int) -> None:
        cabinet = await self.get(cabinet_id)
        await self.repo.delete(cabinet)
        await self.session.commit()

    # Все ШУ
    async def list_all(
        self,
        query: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> list[Cabinet]:
        return await self.repo.search(query=query, sort_by=sort_by, sort_order=sort_order)

    # Генерация уникального кода(хранится в кур-коде)
    async def _generate_unique_code(self) -> str:
        while True:
            code = secrets.token_hex(8).upper()
            if await self.repo.find_by_code(code) is None:
                return code
