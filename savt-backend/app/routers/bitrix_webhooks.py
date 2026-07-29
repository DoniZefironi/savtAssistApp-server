from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.services.bitrix_webhook_service import handle_deal_event, handle_task_comment_webhook, verify_token

router = APIRouter(prefix="/webhooks/bitrix", tags=["bitrix webhooks"])


# Исходящий вебхук Bitrix24 на событие ONTASKCOMMENTADD — настраивается в самом
# Bitrix (обработчик события + application_token — добавить в bitrix_incoming_webhook_tokens)
@router.post("/task-comment", status_code=status.HTTP_204_NO_CONTENT)
async def task_comment_webhook(request: Request, background_tasks: BackgroundTasks):
    form = dict(await request.form())
    if not verify_token(form):
        raise HTTPException(status_code=403, detail="Invalid application_token")
    background_tasks.add_task(handle_task_comment_webhook, form)


# Исходящие вебхуки Bitrix24 на события ONCRMDEALADD/ONCRMDEALUPDATE — один и
# тот же обработчик на оба события (см. README «Вебхуки внешних интеграций»
# для инструкции по настройке в Bitrix и нужной области "CRM" у BITRIX_WEBHOOK_URL)
@router.post("/deal", status_code=status.HTTP_204_NO_CONTENT)
async def deal_webhook(request: Request, background_tasks: BackgroundTasks):
    form = dict(await request.form())
    if not verify_token(form):
        raise HTTPException(status_code=403, detail="Invalid application_token")
    background_tasks.add_task(handle_deal_event, form)
