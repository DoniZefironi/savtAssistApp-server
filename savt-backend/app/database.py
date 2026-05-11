from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Создается движ для подключения к бд setting.database_url - строка подключения, echo = True будет логировать все sql запросы
engine = create_async_engine(
    settings.database_url,
    echo=(settings.app_env == "dev"),
)

# Создание сессий
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Бэзик класс для орм моделей, все модели будут наследовать от base, это помогает sqlalchemy(библиотеке) отслеживать их и создавать табличке
class Base(DeclarativeBase):
    pass

# Генератор сессий (вызывается при каждом запросе, оч крут)
async def get_session():
    async with AsyncSessionLocal() as session:
        yield session