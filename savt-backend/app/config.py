from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    app_env: str = "dev"

    jwt_secret_key: str
    jwt_access_token_ttl_minutes: int = 30
    jwt_refresh_token_ttl_days: int = 60

    sms_code_ttl_minutes: int = 10
    sms_code_max_attempts: int = 5
    sms_code_resend_cooldown_seconds: int = 60

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""  
    telegram_webhook_secret: str = ""  

    messenger_link_request_ttl_minutes: int = 15

    firebase_credentials_path: str = ""

    static_link_secret: str = ""

    static_link_ttl_seconds: int = 86400

    static_link_external_ttl_seconds: int = 7776000

    cors_origins: str = "*"

    yandex_folder_id: str = ""
    yandex_api_key: str = ""
    yandex_gpt_model: str = "yandexgpt-lite"

    yandex_storage_bucket: str = ""
    yandex_storage_access_key_id: str = ""
    yandex_storage_secret_access_key: str = ""
    yandex_storage_endpoint_url: str = "https://storage.yandexcloud.net"

    bot_follow_up_minutes: int = 60
    bot_max_attempts: int = 3

    bitrix_webhook_url: str = ""
    bitrix_default_responsible_id: int = 0
    bitrix_default_group_id: int = 0

    bitrix_default_creator_id: int = 0

    bitrix_incoming_webhook_tokens: str = ""

    bitrix_production_years: str = ""

    bitrix_field_production_number: str = ""

    bitrix_field_shipment_planned: str = ""

    bitrix_field_shipment_actual: str = ""

    telemetry_webhook_secret: str = ""

    telemetry_history_retention_days: int = 14

    project_folders_root: str = ""

    bitrix_project_code_key: str = ""

    bitrix_project_code_pepper: str = "3.1415"

    promo_messages_file: str = ""

    promo_auto_send_hour: str = ""

    # Служебный аккаунт для входа в приложение управления SIM-картами — у него
    # своя JWT-авторизация (POST /api/User/login), не статический токен, см.
    # app/services/sim_service.py
    sim_service_base_url: str = "http://10.1.0.67:5000"
    sim_service_login: str = ""
    sim_service_password: str = ""
    # Веб-интерфейс SimApi (не API) — выбор конкретной SIM там открывает модалку
    # без отражения в URL, поэтому это ссылка на список карт целиком, не на
    # конкретную запись (прямого deep-link на одну SIM в их фронте нет)
    sim_service_frontend_url: str = "http://10.1.0.67:3000/admin/cards"


settings = Settings()