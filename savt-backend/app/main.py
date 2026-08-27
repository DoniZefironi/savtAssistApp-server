import logging
# для управления ресурсами, требующих логики запуска и разборки, в текущем случае соединение с бд и кэши
from contextlib import asynccontextmanager
# планировщик, модуль используется для планирования задач в приложении, которые используют модуль asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
# фреймворк для создания апи и библиотека для запросов
from fastapi import FastAPI, Request
# для управления механизмом CORS
from fastapi.middleware.cors import CORSMiddleware
# для получения json ответов
from fastapi.responses import JSONResponse
# для статических файлов
from fastapi.staticfiles import StaticFiles
# для лимитов скорости запросов
from slowapi.errors import RateLimitExceeded
# для ограничения кол-во запросов
from slowapi.middleware import SlowAPIMiddleware
# используется для написания sql-запросов
from sqlalchemy import text
# для работы с nginx
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.core.exceptions import (
    AlreadyExistsError,
    AuthenticationError,
    InvalidCodeError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from app.database import engine
from app.routers import auth as auth_router
from app.routers import admin_cabinets as admin_cabinets_router
from app.routers import admin_cabinet_requests as admin_cabinet_requests_router
from app.routers import admin_users as admin_users_router
from app.routers import qr as qr_router
from app.routers import admin_documents as admin_documents_router
from app.routers import documents as documents_router
from app.routers import favorites as favorites_router
from app.routers import tags as tags_router
from app.routers import chats as chats_router
from app.routers import operator as operator_router
from app.routers import service_requests as service_requests_router
from app.routers import notifications as notifications_router
from app.routers import admin_audit as admin_audit_router
from app.routers import admin_kb as admin_kb_router
from app.routers import kb as kb_router
from app.routers import admin_faq as admin_faq_router
from app.routers import faq as faq_router
from app.routers import cabinets as cabinets_router
from app.routers import upload as upload_router
from app.routers import admin_bot as admin_bot_router
from app.routers import admin_dashboard as admin_dashboard_router
from app.routers import operator_events as operator_events_router
from app.routers import user_events as user_events_router
from app.routers import projects as projects_router
from app.routers import admin_projects as admin_projects_router
from app.routers import admin_project_requests as admin_project_requests_router
from app.routers import bitrix_webhooks as bitrix_webhooks_router
from app.routers import messenger_webhooks as messenger_webhooks_router
from app.routers import admin_phone_change_requests as admin_phone_change_requests_router
from app.routers import telemetry_webhooks as telemetry_webhooks_router
from app.routers import admin_telemetry as admin_telemetry_router
from app.services.messenger_service import MessengerSendError
from app.core.firebase import init_firebase
from app.services.warranty_scheduler import check_warranty_expiry
from app.services.project_folder_service import sync_all_project_folders
from app.services.service_request_service import sync_statuses_from_bitrix
from app.services import promo_service
from app.core.limiter import limiter
from app.database import AsyncSessionLocal

# настройки логгера, применяется один раз при старте приложения и определяет, как выглядит и куда идут все логи, у которых нет своего 
# отдельного обработчика. level=logging.INFO — включает логи уровня INFO и выше (INFO, WARNING, ERROR, CRITICAL), format - как выглядит лог
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# для предотвращения дублирования в логах sql. 
# logging.getLogger("sqlalchemy.engine.Engine") - получает тот логгер, в который пишет sqlalchemy
# .propagate = False - запрещает этому логгеру передавать записи в корневой логгер
logging.getLogger("sqlalchemy.engine.Engine").propagate = False
# создание логгера
logger = logging.getLogger(__name__)

# функция бота, ничего не возращает
async def _bot_follow_up_job() -> None:
    # открывает ссесию БД на время задачи
    async with AsyncSessionLocal() as session:
        # импорт send_follow_up внутри функции, для защиты от циклического импорта
        from app.services.bot_service import send_follow_up
        # выбирает чаты, где бот ещё активен
        await send_follow_up(session)

# ежедневная чистка старых записей телемтрии ШУ - иначе cabinet_telemetry_events растёт бесконечно
async def _telemetry_history_prune_job() -> None:
    # открывает ссесию БД на время задачи
    async with AsyncSessionLocal() as session:
        # импорт внутри функции
        from app.services.telemetry_service import prune_old_telemetry_history
        # возращает число удалееных строк
        deleted = await prune_old_telemetry_history(session)
        # логировать только если что-то удалили
        if deleted:
            # логирование, что ещё сказать то
            logger.info("Очистка истории телеметрии: удалено %d старых записей", deleted)


# жизненный цикл приложения, выполняется один раз при старте и один раз при остановке
# проверка БД, инициализация файрбазе, системный пользователь-бот, регистрация всех фоновых задач, корректное закрытие ресурсов
@asynccontextmanager
async def lifespan(app: FastAPI):
    # не дает приложению подняться, если бд недоступно
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    # пуш-уведомление, инициализируется один раз на процесс    
    init_firebase(settings.firebase_credentials_path)

    # Создаём системного пользователя Ася
    async with AsyncSessionLocal() as session:
        from app.services.bot_service import ensure_bot_user
        await ensure_bot_user(session)

    # планировщик задач
    scheduler = AsyncIOScheduler()
    # проверка истечения гарантии
    scheduler.add_job(check_warranty_expiry, "cron", hour=9, minute=0)
    # синхронизация папок проектов
    scheduler.add_job(sync_all_project_folders, "cron", hour=2, minute=0)
    # синхронизация с битриксом
    scheduler.add_job(sync_statuses_from_bitrix, "interval", minutes=15)
    # синрхонизация бота с чатами
    scheduler.add_job(_bot_follow_up_job, "interval", minutes=10)
    # чистка старой телеметрии
    scheduler.add_job(_telemetry_history_prune_job, "cron", hour=3, minute=0)

    # реклама рассылается автоматически, только если час задан явно: она уходит
    # живым людям, включать её должно быть осознанным действием
    promo_hour = promo_service.auto_send_hour()
    if promo_hour is not None:
        scheduler.add_job(promo_service.send_random_scheduled, "cron", hour=promo_hour, minute=0)
        logger.info("Автоматическая рассылка рекламы включена: ежедневно в %02d:00", promo_hour)

    # приложение работает
    scheduler.start()

    yield

    # при остановке приложения останавливает и закрывает пул соединений с БД
    scheduler.shutdown()
    await engine.dispose()

# Создание приложения с названием SAVT Assist API и привязка к lifespan
app = FastAPI(title="SAVT Assist API", lifespan=lifespan)

# для того, чтобы видеть настоящий ip клиента, от этого зависит рэйт лимит и логи
# ProxyHeadersMiddleware - переписывает request.client.host на значение из заголовка, который приоставляет nginx.
# без этого весь трафик для приложения выглядил бы так, будто всё идет с одного ip
# trusted_hosts="172.16.0.0/12" - доверять этому заголовку разрешено, только если запрос физически пришел с адреса из этого диапазона
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="172.16.0.0/12")

# обработчик ошибки
@app.exception_handler(RateLimitExceeded)
# перехватывает исключение рейт лимита, которое кидает slowapi, когда лимит превышен
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    # подставляет в сообщение, чтобы клиент видел, какой именно лимит сработал
    response = JSONResponse(
        status_code=429,
        content={"detail": f"Слишком много запросов. Попробуйте позже ({exc.detail})"},
    )
    # дописывает в ответ стандартные заголовки
    return limiter._inject_headers(response, request.state.view_rate_limit)

# место, откуда достают все внутренние механизмы slowapi (мидлваре, обработчик исключений, декоратор)
app.state.limiter = limiter
# движок, который проверяет лимиты на каждый запрос
app.add_middleware(SlowAPIMiddleware)

# CORS — разрешаем запросы с веб-версии
# settings.cors_origins - строка из енв, домены через запятую
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    # список доменов, которым разрешено обращаться к апи из браузера
    allow_origins=_origins,
    # разрешает отправку кук и авторизационных заголовков вместе с кросс-доменным запросом
    allow_credentials=True,
    # разрешает все методы
    allow_methods=["*"],
    # разрешает все заголовки
    allow_headers=["*"],
)

# Обработка исключений

# 404 - не нашлось
@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})

# 409 - конфлик, уже сущестует что-та
@app.exception_handler(AlreadyExistsError)
async def already_exists_handler(_: Request, exc: AlreadyExistsError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})

# 403 - доступ запрещен
@app.exception_handler(PermissionDeniedError)
async def permission_denied_handler(_: Request, exc: PermissionDeniedError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})

# 401 - не авторизован
@app.exception_handler(AuthenticationError)
async def authentication_error_handler(_: Request, exc: AuthenticationError):
    return JSONResponse(status_code=401, content={"detail": str(exc)})

# 400 - неверный код или запрос
@app.exception_handler(InvalidCodeError)
async def invalid_code_handler(_: Request, exc: InvalidCodeError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

# 429 - слишком много запросов, овер
@app.exception_handler(RateLimitError)
async def rate_limit_handler(_: Request, exc: RateLimitError):
    return JSONResponse(status_code=429, content={"detail": str(exc)})

# 503 - не удалось отправить код в Telegram
@app.exception_handler(MessengerSendError)
async def messenger_send_error_handler(_: Request, exc: MessengerSendError):
    return JSONResponse(
        status_code=503,
        content={"detail": "Не удалось отправить код. Попробуйте позже."},
    )

# Подключение роутеров
app.include_router(auth_router.router)
app.include_router(admin_cabinets_router.router)
app.include_router(admin_cabinet_requests_router.router)
app.include_router(admin_users_router.router)
app.include_router(qr_router.router)
app.include_router(admin_documents_router.router)
app.include_router(documents_router.router)
app.include_router(favorites_router.router)
app.include_router(tags_router.router)
app.include_router(chats_router.router)
app.include_router(operator_router.router)
app.include_router(service_requests_router.router)
app.include_router(notifications_router.router)
app.include_router(admin_audit_router.router)
app.include_router(admin_kb_router.router)
app.include_router(kb_router.router)
app.include_router(admin_faq_router.router)
app.include_router(faq_router.router)
app.include_router(cabinets_router.router)
app.include_router(upload_router.router)
app.include_router(admin_bot_router.router)
app.include_router(admin_dashboard_router.router)
app.include_router(operator_events_router.router)
app.include_router(user_events_router.router)
app.include_router(projects_router.router)
app.include_router(admin_projects_router.router)
app.include_router(admin_project_requests_router.router)
app.include_router(bitrix_webhooks_router.router)
app.include_router(messenger_webhooks_router.router)
app.include_router(admin_phone_change_requests_router.router)
app.include_router(telemetry_webhooks_router.router)
app.include_router(admin_telemetry_router.router)
app.mount("/static", StaticFiles(directory="/code/uploads"), name="static")

# Бэзик эндпоинты
@app.get("/")
async def root():
    return {"service": "savt-assist", "status": "ok"}

# проверка работы сервера
@app.get("/health")
async def health():
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        return {"app": "ok", "db": result.scalar() == 1}