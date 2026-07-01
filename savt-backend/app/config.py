from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # База данных
    database_url: str
    app_env: str = "dev"

    # JWT
    jwt_secret_key: str
    jwt_access_token_ttl_minutes: int = 30
    jwt_refresh_token_ttl_days: int = 60

    # SMS-коды
    sms_code_ttl_minutes: int = 10
    sms_code_max_attempts: int = 5
    sms_code_resend_cooldown_seconds: int = 60

    # SMS сервис для кодиков
    sms_provider: str = "smscenter"  # "mock" | "smscenter"
    smscenter_login: str = ""
    smscenter_password: str = ""
    smscenter_sender: str = ""
    smscenter_base_url: str = "https://smscentre.by"

    # Firebase
    firebase_credentials_path: str = ""

    # CORS — через запятую: https://admin.example.com,http://localhost:3000
    # Поставь * чтобы разрешить всем (только для разработки)
    cors_origins: str = "*"

    # Яндекс API
    yandex_folder_id: str = ""
    yandex_api_key: str = ""
    yandex_gpt_model: str = "yandexgpt-lite"

    # Yandex Object Storage — для распознавания голосовых > 1 МБ (longRunningRecognize)
    yandex_storage_bucket: str = ""
    yandex_storage_access_key_id: str = ""
    yandex_storage_secret_access_key: str = ""
    yandex_storage_endpoint_url: str = "https://storage.yandexcloud.net"

    # Бот
    bot_follow_up_minutes: int = 60
    bot_max_attempts: int = 3

    # Bitrix24 — создание задач по заявкам на обслуживание ШУ
    bitrix_webhook_url: str = ""
    bitrix_default_responsible_id: int = 0
    bitrix_default_group_id: int = 0


settings = Settings()