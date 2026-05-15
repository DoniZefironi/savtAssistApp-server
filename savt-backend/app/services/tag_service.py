from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.repositories.tag import TagRepository
from app.schemas.tags import TagCreateIn, TagOut


class TagService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = TagRepository(session)

    async def list_all(self) -> list[TagOut]:
        tags = await self.repo.get_all()
        return [TagOut.model_validate(t) for t in tags]

    async def create(self, data: TagCreateIn) -> TagOut:
        existing = await self.repo.get_by_name(data.name)
        if existing is not None:
            raise AlreadyExistsError(f"Тег '{data.name}' уже существует")
        tag = await self.repo.create(data.name)
        await self.session.commit()
        return TagOut.model_validate(tag)

    async def delete(self, tag_id: int) -> None:
        tag = await self.repo.get_by_id(tag_id)
        if tag is None:
            raise NotFoundError("Тег не найден")
        await self.repo.delete(tag)
        await self.session.commit()

    async def set_document_tags(self, document_id: int, tag_ids: list[int]) -> None:
        await self.repo.set_document_tags(document_id, tag_ids)
        await self.session.commit()

    async def set_article_tags(self, article_id: int, tag_ids: list[int]) -> None:
        await self.repo.set_article_tags(article_id, tag_ids)
        await self.session.commit()
