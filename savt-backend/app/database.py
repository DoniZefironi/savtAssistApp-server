from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Создается движ для подключения к бд setting.database_url - строка подключения, echo = True будет логировать все sql запросы
#
# Пул соединений — раньше был на дефолтах SQLAlchemy (pool_size=5, max_overflow=10,
# pool_pre_ping выключен), никем осознанно не выбранных под нагрузку этого
# сервиса. api — один процесс (uvicorn без --workers, см. Dockerfile), весь
# трафик идёт через этот единственный пул:
# - pool_size/max_overflow подняты вдвое — один воркер обслуживает всё разом
#   (обычные запросы + WS/SSE + фоновые задачи APScheduler + приём телеметрии);
# - pool_pre_ping=True — проверяет соединение перед использованием и молча
#   переоткрывает, если оно протухло (после простоя, NAT/firewall тихо роняет
#   долгие idle-соединения — на этом сервере уже были реальные сетевые
#   инциденты, см. историю с netplan/маршрутизацией);
# - pool_recycle=1800 — на всякий случай не держим соединение дольше 30 минут
#   даже если pre-ping почему-то не поймал протухание раньше.
engine = create_async_engine(
    settings.database_url,
    echo=(settings.app_env == "dev"),
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
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