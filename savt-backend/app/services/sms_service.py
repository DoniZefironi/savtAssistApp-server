import logging

logger = logging.getLogger(__name__)


class SmsService:
    async def send_verification_code(self, phone: str, code: str) -> None:
        logger.info(f"[SMS] → {phone}: код {code}")
        print(f"\n>>> SMS на {phone}: КОД = {code} <<<\n", flush=True)


sms_service = SmsService()