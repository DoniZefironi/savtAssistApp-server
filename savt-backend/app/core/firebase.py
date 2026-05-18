import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_initialized = False


def init_firebase(credentials_path: str) -> None:
    global _initialized
    if _initialized:
        return
    if not credentials_path or not Path(credentials_path).exists():
        logger.warning("Firebase credentials not found — push уведомления отключены")
        return
    try:
        import firebase_admin
        from firebase_admin import credentials
        cred = credentials.Certificate(credentials_path)
        firebase_admin.initialize_app(cred)
        _initialized = True
        logger.info("Firebase инициализирован")
    except Exception as e:
        logger.error(f"Ошибка инициализации Firebase: {e}")


def is_firebase_ready() -> bool:
    return _initialized
