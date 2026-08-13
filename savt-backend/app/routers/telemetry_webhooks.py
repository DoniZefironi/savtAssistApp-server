from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.schemas.telemetry import TelemetryWebhookIn
from app.services.telemetry_service import TelemetryIngestService, verify_telemetry_secret

router = APIRouter(prefix="/webhooks/telemetry", tags=["telemetry webhooks"])


# Вебхук от C#-прокси (не от самого ПЛК — он про HTTP ничего не знает). Прокси
# слушает MQTT-топик контроллера и шлёт сюда уже разобранный JSON. Топик прокси
# передаёт как есть, cabinet_id ему неизвестен — ищем ШУ по Cabinet.mqtt_topic.
@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def receive_telemetry(
    payload: TelemetryWebhookIn,
    x_telemetry_secret: str | None = Header(None, alias="X-Telemetry-Secret"),
    session: AsyncSession = Depends(get_session),
):
    if not verify_telemetry_secret(x_telemetry_secret):
        raise HTTPException(status_code=403, detail="Invalid X-Telemetry-Secret")
    await TelemetryIngestService(session).ingest(payload.topic, payload.registers, payload.timestamp)
