import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.services import messenger_service
from app.services.messenger_webhook_service import handle_telegram_update, handle_viber_event

router = APIRouter(prefix="/webhooks", tags=["messenger webhooks"])


# Настраивается через Telegram setWebhook (url + secret_token), см. bitrix_webhooks.py
# для образца — тот же паттерн: синхронная проверка → 403 либо фоновая обработка → 204
@router.post("/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    if not messenger_service.verify_telegram_secret(
        request.headers.get("x-telegram-bot-api-secret-token")
    ):
        raise HTTPException(status_code=403, detail="Invalid secret token")
    payload = await request.json()
    background_tasks.add_task(handle_telegram_update, payload)


# Настраивается через Viber set_webhook. Подпись проверяется по сырому телу
# запроса — поэтому читаем request.body() до какого-либо JSON-парсинга
@router.post("/viber", status_code=status.HTTP_204_NO_CONTENT)
async def viber_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    if not messenger_service.verify_viber_signature(
        raw_body, request.headers.get("x-viber-content-signature")
    ):
        raise HTTPException(status_code=403, detail="Invalid signature")
    payload = json.loads(raw_body)
    background_tasks.add_task(handle_viber_event, payload)
