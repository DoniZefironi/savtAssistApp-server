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
class SmsCenterProvider(SmsProvider):

    def __init__(self, login: str, password: str, sender: str, base_url: str):
        if not login or not password:
            raise ValueError("SMSCENTER_LOGIN/SMSCENTER_PASSWORD не заданы") # А логин с паролем есть?
        self._login = login
        self._password = password
        self._sender = sender
        self._base_url = base_url.rstrip("/")

    async def send_verification_code(self, phone: str, code: str) -> None:
        # Очистка номера, "+" нам не нужен
        phone_clean = phone.lstrip("+")

        # Формируем сообщение
        message = f"Код подтверждения SAVT Assist: {code}"

        # Параметры запроса (fmt=1 — ответ в виде csv: <id>,<cnt>,<cost>,<balance> либо <id>,-<код ошибки>)
        params = {
            "login": self._login,
            "psw": self._password,
            "phones": phone_clean,
            "mes": message,
            "fmt": "1",
            "charset": "utf-8",
            "cost": "3",
        }
        if self._sender:
            params["sender"] = self._sender # имя отправителя (Sender ID)

        url = f"{self._base_url}/sys/send.php"

        # Обрабатываем хттп запрос с таймаутом 10 сек(чтоб когда-то это закончилось)
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(url, params=params)
            except httpx.RequestError as e:
                logger.error(f"SMSCenter сетевая ошибка: {e}")
                raise SmsSendError("Не удалось отправить SMS") from e

        # Проверка статуса
        if response.status_code != 200:
            logger.error(f"SMSCenter HTTP {response.status_code}: {response.text}")
            raise SmsSendError(f"SMS-провайдер вернул ошибку HTTP {response.status_code}")

        # Разбираем csv-ответ: <id>,<cnt>,<cost>,<balance> или <id>,-<код ошибки>
        parts = response.text.strip().split(",")
        try:
            sms_id, second = parts[0], int(parts[1])
        except (IndexError, ValueError):
            logger.error(f"SMSCenter не распознан ответ: {response.text}")
            raise SmsSendError("Не удалось разобрать ответ SMS-провайдера")

        if second < 0:
            logger.error(f"SMSCenter ошибка №{-second}: {response.text}")
            raise SmsSendError(f"SMS-провайдер вернул ошибку №{-second}")

        # Успешнооо, логируем
        logger.info(f"SMSCenter отправлено: phone={phone_clean}, sms_id={sms_id}, cnt={second}")


class SmsSendError(Exception):
    """Ошибка отправки SMS."""

# Фабрика(сама решаем кого нам подсунуть, тут smscenter) провайдеров, если вдруг у нас что-то кроме smscenter, то запускаем mock(тестовый режим с логами)
def _build_provider() -> SmsProvider:
    if settings.sms_provider == "smscenter":
        return SmsCenterProvider(
            login=settings.smscenter_login,
            password=settings.smscenter_password,
            sender=settings.smscenter_sender,
            base_url=settings.smscenter_base_url,
        )
    return MockSmsProvider()

# Экземпляр
sms_service: SmsProvider = _build_provider()