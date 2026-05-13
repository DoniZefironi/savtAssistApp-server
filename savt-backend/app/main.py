from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

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
from app.routers import cabinets as cabinets_router
from app.routers import upload as upload_router
from app.services.sms_service import SmsSendError

# Управление жизненным циклом приложения, проверяет подключение к бд и закрывает соединение с бд
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    yield
    await engine.dispose()

# Создание приложения с названием SAVT Assist API и привязка к lifespan
app = FastAPI(title="SAVT Assist API", lifespan=lifespan)

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
app.include_router(cabinets_router.router)
app.include_router(upload_router.router)
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