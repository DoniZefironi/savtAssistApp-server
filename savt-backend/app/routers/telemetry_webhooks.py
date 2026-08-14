from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.core.limiter import limiter
from app.schemas.telemetry import TelemetryTargetOut, TelemetryWebhookIn
from app.services.telemetry_service import TelemetryIngestService, verify_telemetry_secret

router = APIRouter(prefix="/webhooks/telemetry", tags=["telemetry webhooks"])


def _check_secret(x_telemetry_secret: str | None) -> None:
    if not verify_telemetry_secret(x_telemetry_secret):
        raise HTTPException(status_code=403, detail="Invalid X-Telemetry-Secret")


# Список брокеров, к которым прокси должен быть подключён прямо сейчас — свой
# у каждого ШУ (host/port/topic/логин-пароль), не общий конфиг на всё. Прокси
# дёргает это периодически (не хранит список у себя статично), чтобы новый ШУ
# с указанным брокером подхватывался без переразвёртывания самого прокси.
@router.get("/targets", response_model=list[TelemetryTargetOut])
async def get_telemetry_targets(
    x_telemetry_secret: str | None = Header(None, alias="X-Telemetry-Secret"),
    session: AsyncSession = Depends(get_session),
):
    _check_secret(x_telemetry_secret)
    return await TelemetryIngestService(session).list_targets()


# Вебхук от C#-прокси (не от самого ПЛК — он про HTTP ничего не знает). Прокси
# слушает MQTT-топик контроллера и шлёт сюда уже разобранный JSON. Топик прокси
# передаёт как есть, cabinet_id ему неизвестен — ищем ШУ по Cabinet.mqtt_topic.
#
# Свой (сильно повышенный) лимит вместо общего 200/мин — см. инцидент
# 2026-08-14: прокси слал сюда чаще 200/мин суммарно по всем подключённым ШУ
# (нормальный рост нагрузки с числом настроенных брокеров, не атака) и упирался
# в дефолтный лимит. Ключ лимита — IP, а не пользователь: тут это фиксированный
# внутренний адрес контейнера telemetry-proxy в docker-сети, не что-то, чем
# может воспользоваться атакующий снаружи (сам вебхук всё равно закрыт
# отдельным секретом в заголовке, см. _check_secret)
@router.post("", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("3000/minute")
async def receive_telemetry(
    request: Request,
    payload: TelemetryWebhookIn,
    x_telemetry_secret: str | None = Header(None, alias="X-Telemetry-Secret"),
    session: AsyncSession = Depends(get_session),
):
    _check_secret(x_telemetry_secret)
    await TelemetryIngestService(session).ingest(payload.topic, payload.registers, payload.timestamp)
