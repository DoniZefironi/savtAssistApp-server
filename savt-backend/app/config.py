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

    # Доставка кода подтверждения телефона — Telegram/Viber бот (SMS отключено полностью)
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""  # без @, для t.me/<username>?start=...
    telegram_webhook_secret: str = ""  # X-Telegram-Bot-Api-Secret-Token

    viber_bot_token: str = ""
    viber_bot_uri: str = ""  # chatURI из настроек Viber Public Account, для viber://pa?chatURI=...

    # Сколько живёт токен "рукопожатия" (пока пользователь не откроет deep-link на бота)
    messenger_link_request_ttl_minutes: int = 15

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
    # Постановщик задачи (CREATED_BY) — отдельно от исполнителя (RESPONSIBLE_ID).
    # Если не задан — Bitrix сам подставит технического пользователя вебхука.
    bitrix_default_creator_id: int = 0
    # Секреты для проверки входящих вебхуков из Bitrix (комментарии к задаче,
    # обновление сделки и т.п.) — через запятую. Bitrix генерирует свой
    # application_token на КАЖДОЕ правило исходящего вебхука отдельно (свой
    # вручную не задать), поэтому здесь может быть несколько значений сразу —
    # по одному на каждое настроенное правило.
    bitrix_incoming_webhook_tokens: str = ""

    # Путь внутри контейнера к смонтированной сетевой шаре NAS с папками проектов
    # (см. docker-compose.yml — CIFS-том). Пусто = папки проектов не создаются вовсе.
    project_folders_root: str = ""

    # Обратимое шифрование номера проекта из Bitrix (например "26_138") в unique_code —
    # ключ Fernet, генерировать: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    bitrix_project_code_key: str = ""
    # Фиксированная (не секретная, не случайная) добавка к номеру перед шифрованием —
    # просто чтобы шифровался не голый предсказуемый номер. Значение не обязано быть
    # именно "3.1415", но должно оставаться неизменным — смена ломает расшифровку
    # уже выданных кодов.
    bitrix_project_code_pepper: str = "3.1415"


settings = Settings()