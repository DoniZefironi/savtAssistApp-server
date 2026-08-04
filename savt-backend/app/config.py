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

    # Доставка кода подтверждения телефона — Telegram-бот (SMS отключено полностью,
    # Viber удалён: подтвердить владение номером через него нечем, аналога
    # request_contact у Viber нет)
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""  # без @, для t.me/<username>?start=...
    telegram_webhook_secret: str = ""  # X-Telegram-Bot-Api-Secret-Token

    # Сколько живёт токен "рукопожатия" (пока пользователь не откроет deep-link на бота)
    messenger_link_request_ttl_minutes: int = 15

    # Firebase
    firebase_credentials_path: str = ""

    # Подпись ссылок на /static/ (nginx secure_link, см. app/core/signed_urls.py).
    # Секрет ОБЯЗАН совпадать со значением в secure_link_md5 в nginx*.conf.
    # Пусто = ссылки отдаются без подписи (локальный запуск без nginx); на стенде,
    # где nginx подпись проверяет, пустой секрет означает 403 на все файлы.
    # Генерировать: python -c "import secrets; print(secrets.token_urlsafe(32))"
    static_link_secret: str = ""
    # Сколько живёт ссылка, отданная в API. Сутки — чтобы открытый экран чата
    # не терял картинки, пока клиент не перезапросит сообщения.
    static_link_ttl_seconds: int = 86400
    # Для ссылок, уходящих во внешние системы (комментарии Bitrix-задач):
    # их открывают спустя дни, поэтому срок заметно длиннее. 90 дней.
    static_link_external_ttl_seconds: int = 7776000

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

    # Годы производства, которые импортируем, через запятую: "25,26,27".
    # Пусто = любой год. Номер проекта всегда начинается с двух цифр года и "_"
    # ("26_138"), поэтому раньше здесь стоял ровно один префикс и работал как
    # отсечка — сделки других лет не импортировались вовсе. Теперь номер берётся
    # из отдельного поля сделки (bitrix_field_production_number), и ограничивать
    # год нужно разве что перед массовым импортом, чтобы не затянуть весь архив.
    bitrix_production_years: str = ""

    # Коды пользовательских полей сделки. Свои у каждого портала (см. в README
    # команду для их поиска через crm.deal.fields), поэтому только в настройках.
    # Номер в производство — "26_138". Пусто = берём из названия сделки, как раньше
    bitrix_field_production_number: str = ""
    # Планируемая дата отгрузки
    bitrix_field_shipment_planned: str = ""
    # Фактическая дата отгрузки. Можно перечислить несколько кодов через запятую —
    # берётся первое непустое. Нужно на время переезда со старого поля на новое,
    # когда часть сделок заполнена по-старому, а часть уже по-новому.
    bitrix_field_shipment_actual: str = ""

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

    # Подборка рекламных уведомлений. Файл читается заново на каждой отправке,
    # так что правки применяются без перезапуска. Пусто = встроенный
    # app/data/promo_messages.json; свой файл удобно примонтировать томом.
    promo_messages_file: str = ""
    # Час (0-23) ежедневной автоматической рассылки случайной рекламы.
    # Пусто = автоматически не рассылается вовсе, только по кнопке админа —
    # разумный по умолчанию режим: реклама уходит живым людям, включать её
    # должно быть осознанным действием.
    promo_auto_send_hour: str = ""


settings = Settings()