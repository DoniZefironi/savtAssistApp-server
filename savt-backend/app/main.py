from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
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
from app.services.sms_service import SmsSendError
from app.core.firebase import init_firebase
from app.services.warranty_scheduler import check_warranty_expiry
from app.core.limiter import limiter
from app.database import AsyncSessionLocal


async def _bot_follow_up_job() -> None:
    async with AsyncSessionLocal() as session:
        from app.services.bot_service import send_follow_up
        await send_follow_up(session)


# Управление жизненным циклом приложения, проверяет подключение к бд и закрывает соединение с бд
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    init_firebase(settings.firebase_credentials_path)

    # Создаём системного пользователя Ася
    async with AsyncSessionLocal() as session:
        from app.services.bot_service import ensure_bot_user
        await ensure_bot_user(session)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_warranty_expiry, "cron", hour=9, minute=0)
    scheduler.add_job(_bot_follow_up_job, "interval", minutes=10)
    scheduler.start()

    yield

    scheduler.shutdown()
    await engine.dispose()

# Создание приложения с названием SAVT Assist API и привязка к lifespan
app = FastAPI(title="SAVT Assist API", lifespan=lifespan)

# Читаем реальный IP из X-Forwarded-For (Nginx proxy)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS — разрешаем запросы с веб-версии
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
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

# 503 - сервис смс недоступен, мб баланс минус
@app.exception_handler(SmsSendError)
async def sms_send_error_handler(_: Request, exc: SmsSendError):
    return JSONResponse(
        status_code=503,
        content={"detail": "SMS-сервис временно недоступен. Попробуйте позже."},
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
app.mount("/static", StaticFiles(directory="/code/uploads"), name="static")

# Бэзик эндпоинты
@app.get("/")
async def root():
    return {"service": "savt-assist", "status": "ok"}


@app.get("/health")
async def health():
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        return {"app": "ok", "db": result.scalar() == 1}