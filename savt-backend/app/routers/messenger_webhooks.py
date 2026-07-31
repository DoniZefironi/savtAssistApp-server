from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.services import messenger_service
from app.services.messenger_webhook_service import handle_telegram_update

router = APIRouter(prefix="/webhooks", tags=["messenger webhooks"])


# Настраивается через Telegram setWebhook (url + secret_token), см. bitrix_webhooks.py
# для образца — тот же паттерн: синхронная проверка → 403 либо фоновая обработка → 204
#
# Вебхук Viber удалён вместе с самим каналом: подтвердить владение номером через
# Viber нечем (аналога Telegram request_contact у него нет), а без подтверждения
# регистрация позволяла занять любой чужой номер.
@router.post("/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    if not messenger_service.verify_telegram_secret(
        request.headers.get("x-telegram-bot-api-secret-token")
    ):
        raise HTTPException(status_code=403, detail="Invalid secret token")
    payload = await request.json()
    background_tasks.add_task(handle_telegram_update, payload)
