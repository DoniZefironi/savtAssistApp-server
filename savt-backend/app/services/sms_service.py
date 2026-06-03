import logging
from abc import ABC, abstractmethod

import httpx

from app.config import settings


logger = logging.getLogger(__name__)

# Бэзик класс, определяет интерфейс для всех sms-провайдеров, заставляет реализовывать метод send-verification_code
class SmsProvider(ABC):
    @abstractmethod
    async def send_verification_code(self, phone: str, code: str) -> None:
        """Отправляет код подтверждения на указанный телефон."""

# Для тестирования создания кодов, никуда не отправляет коды, это mock в .env, если вдруг sms-сервис вмэр, 
# для получения кода в консоле написать "docker compose logs api", там будет что-то SMS на +375XXXXXXXX КОД = 228228
class MockSmsProvider(SmsProvider):
    async def send_verification_code(self, phone: str, code: str) -> None:
        logger.info(f"[MOCK SMS] {phone}: код {code}")
        print(f"\n>>> SMS на {phone}: КОД = {code} <<<\n", flush=True)

# Реальный провайдер
class SmsByProvider(SmsProvider):

    def __init__(self, token: str, alphaname: str, base_url: str):
        if not token:
            raise ValueError("SMS_BY_TOKEN не задан") # А токен есть?
        self._token = token
        self._alphaname = alphaname
        self._base_url = base_url.rstrip("/")

    async def send_verification_code(self, phone: str, code: str) -> None:
        # Очистка номера, "+" нам не нужен
        phone_clean = phone.lstrip("+")

        # Формируем сообщение
        message = f"Код подтверждения SAVT Assist: {code}"

        # Параметры запроса
        params = {
            "token": self._token,
            "message": message,
            "phone": phone_clean,
        }
        if self._alphaname:
            params["alphaname_id"] = self._alphaname # вместо номера телефона кидаем альфа-имя(оно будет показываться как отправитель смс)

        url = f"{self._base_url}/sendQuickSMS"

        # Обрабатываем хттп запрос с таймаутом 10 сек(чтоб когда-то это закончилось)
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(url, params=params)
            except httpx.RequestError as e:
                logger.error(f"SMS.by сетевая ошибка: {e}")
                raise SmsSendError("Не удалось отправить SMS") from e

        # Проверка статуса
        if response.status_code != 200:
            logger.error(f"SMS.by HTTP {response.status_code}: {response.text}")
            raise SmsSendError(f"SMS-провайдер вернул ошибку HTTP {response.status_code}")

        # Пытаемся запарсить
        try:
            data = response.json()
        except ValueError:
            logger.error(f"SMS.by не-JSON ответ: {response.text}")
            raise SmsSendError("Не удалось разобрать ответ SMS-провайдера")

        # Проверяем на ошибки в ответе апи
        if "error" in data:
            logger.error(f"SMS.by ошибка: {data}")
            raise SmsSendError(f"SMS-провайдер: {data.get('error')}")

        # Успешнооо, логируем
        logger.info(f"SMS.by отправлено: phone={phone_clean}, sms_id={data.get('sms_id')}")


class SmsSendError(Exception):
    """Ошибка отправки SMS."""

# Фабрика(сама решаем кого нам подсунуть, тут sms_by) провайдеров, если вдруг у нас что-то кроме sms_by, то запускаем mock(тестовый режим с логами)
def _build_provider() -> SmsProvider:
    if settings.sms_provider == "sms_by":
        return SmsByProvider(
            token=settings.sms_by_token,
            alphaname=settings.sms_by_alphaname,
            base_url=settings.sms_by_base_url,
        )
    return MockSmsProvider()

# Экземпляр
sms_service: SmsProvider = _build_provider()