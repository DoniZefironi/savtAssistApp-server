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


settings = Settings()