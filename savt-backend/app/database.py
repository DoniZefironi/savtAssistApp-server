from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# создание асинхронного движка, который подключается к постгре с пулом соединений, настроенным под то, 
# что этот единственный процесс тянет сразу всё (рест апи, ссе, вебхуки телеметрии)
engine = create_async_engine(
    # строка подключение из енв
    settings.database_url,
    # логирование каждого sql запроса
    echo=(settings.app_env == "dev"),
    # кол-во соединений дежаться открытыми постоянно
    pool_size=10,
    # сколько ещё можно открыть сверх при пиковой нагрузке
    max_overflow=20,
    # перед отдачей соединения из пула, движок сначала шлёт SELECT 1, чтобы проверить, что оно живое, если не живое - открывает
    pool_pre_ping=True,
    # принудительное пересоздание соединения, если ему больше 30 минут (1800 сек)
    pool_recycle=1800,
)

# завод сессий, то, чем создается каждая отдельная рабочая единица для запроса к бд.
AsyncSessionLocal = async_sessionmaker(
    # привязка к движку пула
    engine,
    # класс сессии
    class_=AsyncSession,
    # позволяет объектам оставаться живыми со своими текущими значениями атрибутов 
    # и после коммита не нужен новый SELECT, чтобы прочитать
    expire_on_commit=False,
)

# предок для всех орм
class Base(DeclarativeBase):
    pass