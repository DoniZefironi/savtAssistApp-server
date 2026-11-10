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

    # Самый большой файл, который может загрузить обычный пользователь (мобильное приложение,
    # веб-версия): обычные файлы (фото, документы) и отдельно видео. Сотрудники (оператор, админ,
    # суперадмин) грузят до 500 МБ, голосовые у всех до 25 МБ. Оба лимита должны быть меньше того,
    # что пропускает nginx на 443 (150 МБ вместе с накладными расходами запроса), то есть до 140
    user_upload_max_mb: int = 30
    user_video_upload_max_mb: int = 100
    # Сколько МБ обычный пользователь может загрузить за скользящие сутки (вложения и голосовые
    # вместе); сотрудников не касается. 0 — квота выключена. Без неё один пользователь за
    # минуту заливает на диск гигабайты (лимит запросов считается на IP, а не на объём)
    user_upload_quota_mb_per_day: int = 500

    # Ночная уборка файлов в /uploads, на которые больше не ссылается ни одна запись: off —
    # выключена, dry_run — только пишет в лог, что удалила бы (по умолчанию), delete — удаляет.
    # Любое другое значение работает как dry_run. Берутся только файлы старше
    # ORPHAN_FILES_MIN_AGE_DAYS, за один запуск не больше ORPHAN_FILES_MAX_PER_RUN
    orphan_files_mode: str = "dry_run"
    orphan_files_min_age_days: int = 14
    orphan_files_max_per_run: int = 500

    # Антивирус ClamAV (контейнер clamav в docker-compose.yml) проверяет файлы, загруженные через
    # API. По умолчанию выключен: локальный запуск и тесты обходятся без него
    clamav_enabled: bool = False
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_timeout_seconds: int = 180
    # Если антивирус недоступен: False — файл принимается, ошибка пишется в лог (чат с файлами
    # не должен ломаться из-за упавшего антивируса); True — загрузка отклоняется (503)
    clamav_fail_closed: bool = False

    yandex_folder_id: str = ""
    yandex_api_key: str = ""
    yandex_gpt_model: str = "yandexgpt-lite"
    # Vision-модель для анализа фото в чате бота (app/services/yandex_service.py
    # analyze_image) — отдельный OpenAI-совместимый API Yandex AI Studio, не тот
    # же foundationModels, что у обычного complete(). YandexGPT сама картинки не
    # понимает, поэтому здесь сторонняя модель, хостящаяся у Yandex AI Studio
    yandex_vision_model: str = "gemma-3-27b-it"

    yandex_storage_bucket: str = ""
    yandex_storage_access_key_id: str = ""
    yandex_storage_secret_access_key: str = ""
    yandex_storage_endpoint_url: str = "https://storage.yandexcloud.net"

    bot_follow_up_minutes: int = 60
    bot_max_attempts: int = 3
    # Сколько последних сообщений чата (включая текущее) уходит в LLM как
    # история диалога — чем больше, тем дороже и медленнее каждый запрос
    bot_history_limit: int = 25
    # Потолок сообщений одного пользователя, на которые бот отвечает: каждое — это
    # запросы к Yandex (эмбеддинг, генерация, описание фото, распознавание голоса)
    bot_user_messages_per_minute: int = 10
    bot_user_messages_per_day: int = 300

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
    # full — синхронизируются папки всех проектов. create_only — только папки, которые завёл
    # сам сервер (projects.folder_managed): уже существующие папки он не читает и не меняет,
    # а если папка с именем нового проекта уже есть, не трогает и её
    project_folders_mode: str = "full"
    # Годы проектов через запятую (26 или 26,27), для которых сервер вообще работает с папками:
    # создаёт, синхронизирует, зеркалит. Год — по тому же правилу, что годовая папка (!2026).
    # Пусто — все годы
    project_folders_years: str = ""

    bitrix_project_code_key: str = ""

    bitrix_project_code_pepper: str = "3.1415"

    bitrix_reclamation_entity_type_id: int = 1176

    # Ссылка на карточку рекламации в админке — вставляется в sourceDescription
    # элемента Bitrix, см. reclamation_service._build_bitrix_description
    reclamation_admin_url: str = "http://10.1.0.208:8080/admin/requests"

    # Публичная страница для QR, отсканированного обычной камерой телефона
    # (не в приложении) — см. app/routers/add_qr.py. Сам адрес этой страницы
    # и есть то, что зашито в QR-код (crm.item.add generate_qr), поэтому
    # нужен абсолютный https-адрес нашего сервера, не относительный путь
    public_base_url: str = "https://app.savt.by"
    # Пакет приложения — для intent://-ссылки, которой страница пытается
    # открыть установленное приложение напрямую, минуя её саму
    android_package_name: str = ""
    # Пока нет публикации в Google Play — прямая ссылка на APK (Google Drive
    # и т.п.). Пусто — страница покажет просто текст без кнопки скачивания
    apk_download_url: str = ""

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