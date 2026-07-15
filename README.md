# savtAssistApp-server

## Тестовые пользователи

| Роль     | Логин              | Пароль           |
|----------|--------------------|------------------|
| super    | superadmin         | MyStr0ngPass     |
| admin    | admin              | 123qweASDZXC     |
| operator | operator           | 123qweASDZXC     |
| user     | +375291002030      | qweasdzxc        |

> Эти аккаунты не создаются автоматически миграциями. Администратора и оператора нужно создать через CLI:
> ```bash
> docker exec savt-backend-api-1 python -m app.cli create-admin admin 123qweASDZXC
> docker exec savt-backend-api-1 python -m app.cli create-operator operator 123qweASDZXC
> ```
> Пользователя `+375291002030` — зарегистрировать через `POST /auth/register/start` + `/auth/register/complete`.

---

## Переменные окружения (.env)

| Переменная | Описание |
|---|---|
| `DATABASE_URL` | Строка подключения к PostgreSQL |
| `JWT_SECRET_KEY` | Секрет для подписи JWT |
| `JWT_ACCESS_TOKEN_TTL_MINUTES` | Время жизни access-токена (мин) |
| `JWT_REFRESH_TOKEN_TTL_DAYS` | Время жизни refresh-токена (дни) |
| `CORS_ORIGINS` | Разрешённые origins через запятую |
| `FIREBASE_CREDENTIALS_PATH` | Путь к JSON-ключу Firebase |
| `YANDEX_FOLDER_ID` | Folder ID сервисного аккаунта Yandex Cloud |
| `YANDEX_API_KEY` | API-ключ Yandex Cloud |
| `YANDEX_GPT_MODEL` | Модель YandexGPT (`yandexgpt-lite`) |
| `YANDEX_STORAGE_BUCKET` | Бакет Yandex Object Storage для голосовых >1 МБ (long-running распознавание) |
| `YANDEX_STORAGE_ACCESS_KEY_ID` | Статический ключ доступа (S3-совместимый) к бакету |
| `YANDEX_STORAGE_SECRET_ACCESS_KEY` | Секрет статического ключа доступа к бакету |
| `YANDEX_STORAGE_ENDPOINT_URL` | Endpoint Object Storage (по умолч. `https://storage.yandexcloud.net`) |
| `BOT_FOLLOW_UP_MINUTES` | Через сколько минут бот пишет follow-up (по умолч. 60) |
| `BOT_MAX_ATTEMPTS` | Попыток бота до предложения оператора (по умолч. 3) |
| `BITRIX_WEBHOOK_URL` | URL входящего вебхука Bitrix24 (`https://портал.bitrix24.ru/rest/ID/КОД/`) |
| `BITRIX_DEFAULT_RESPONSIBLE_ID` | ID сотрудника Bitrix24, назначаемого ответственным по всем автосозданным задачам |
| `BITRIX_DEFAULT_GROUP_ID` | ID проекта (рабочей группы) Bitrix24, необязательно — без него задачи создаются без привязки к проекту |
| `APP_ENV` | Окружение (`dev`/`prod`), в `dev` включает SQL-логирование |
| `SMS_PROVIDER` | Провайдер SMS: `mock` (по умолч.) или `smscenter` |
| `SMSCENTER_LOGIN` | Логин аккаунта smscenter.by |
| `SMSCENTER_PASSWORD` | Пароль аккаунта smscenter.by |
| `SMSCENTER_SENDER` | Имя отправителя SMS (Sender ID), необязательно |
| `SMSCENTER_BASE_URL` | Базовый URL API smscenter.by (по умолч. `https://smscentre.by`) |
| `SMS_CODE_TTL_MINUTES` | Срок действия SMS-кода в минутах (по умолч. `10`) |
| `SMS_CODE_MAX_ATTEMPTS` | Максимум попыток ввода SMS-кода (по умолч. `5`) |
| `SMS_CODE_RESEND_COOLDOWN_SECONDS` | Кулдаун повторной отправки SMS-кода в секундах (по умолч. `60`) |

---

## Работа с Docker

Конфигурация разделена на прод и dev:

- `docker-compose.yml` — **продакшн**: без hot-reload, код внутри образа, nginx с HTTPS (`nginx.conf`).
- `docker-compose.dev.yml` — **локальная разработка**: `--reload`, монтирование кода, nginx без TLS (`nginx.dev.conf`).

```bash
# ЛОКАЛЬНАЯ РАЗРАБОТКА — сборка и запуск
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

# ПРОД (на сервере) — сборка и запуск
docker compose up -d --build

# Миграции
docker exec savt-backend-api-1 alembic upgrade head

# Создание новой миграции (после изменения моделей)
docker exec savt-backend-api-1 alembic revision --autogenerate -m "описание"

# Логи
docker compose logs api --tail=20

# Подключение к БД
docker exec -it savt-backend-db-1 psql -U postgres -d savt
# \dt — все таблицы
# \d <таблица> — структура таблицы
# \q — выход
```

> После изменения кода на проде нужен пересбор: `docker compose up -d --build` (код копируется в образ, а не монтируется).

---

## Развёртывание и HTTPS (прод)

Прод-nginx (`nginx.conf`) принимает только HTTPS: порт 80 отдаёт ACME-челленджи и редиректит на 443. Без сертификата контейнер nginx **не стартует** — поэтому порядок первого развёртывания такой:

Какой конфиг nginx использовать, задаётся переменной `NGINX_CONF` в `.env` (по умолчанию `nginx.conf`):

| Конфиг | Когда использовать |
|---|---|
| `nginx.conf` (умолч.) | Прод: 443 с TLS, порт 80 — только редирект и ACME |
| `nginx.http-fallback.conf` | Переходный: порт 443 снаружи ещё не проброшен — API работает и по 80, и по 443 |
| `nginx.dev.conf` | Локальная разработка (подключается автоматически через `docker-compose.dev.yml`) |

Переходный режим (пока сеть/роутер не пробрасывает 443):

```bash
echo "NGINX_CONF=nginx.http-fallback.conf" >> .env
docker compose up -d nginx
```

После проброса 443 вернуть строгий режим: удалить строку `NGINX_CONF` из `.env` и снова `docker compose up -d nginx`.

### 1. Подготовка

```bash
# В .env выставить боевые секреты:
#   JWT_SECRET_KEY  — минимум 64 случайных символа
#   POSTGRES_PASSWORD — длинный случайный пароль
openssl rand -hex 48   # генератор секретов
```

> Если меняете `POSTGRES_PASSWORD` на уже работающей БД, одного .env мало — пароль хранится в самой БД:
> ```bash
> docker exec -it savt-backend-db-1 psql -U postgres -c "ALTER USER postgres PASSWORD 'новый_пароль';"
> # затем обновить POSTGRES_PASSWORD и DATABASE_URL в .env и перезапустить api
> ```

### 2. Получить сертификат (первый раз — standalone, порт 80 должен быть свободен)

```bash
sudo apt install certbot            # если ещё не установлен
docker compose stop nginx           # освободить порт 80
sudo certbot certonly --standalone -d helper.savt.by
mkdir -p certbot-www                # webroot для последующих продлений
docker compose up -d                # nginx стартует уже с сертификатом
```

### 3. Автопродление сертификата

`crontab -e`, добавить:

```
0 4 * * 1 certbot renew --webroot -w /path/to/savt-backend/certbot-www --deploy-hook "docker exec savt-backend-nginx-1 nginx -s reload" >> /var/log/certbot-renew.log 2>&1
```

### 4. Проверка

```bash
curl -I http://helper.savt.by/health    # → 301 на https (в переходном режиме — 200)
curl https://helper.savt.by/health      # → {"app":"ok","db":true}
```

---

# savtAssistApp — сервер мобильного приложения поддержки SAVT

API для управления пользователями, шкафами управления (ШУ), документацией, QR-кодами, чатами, сервисными заявками, уведомлениями, базой знаний и FAQ.

**Роли (иерархия от низшей к высшей):**
1. **Пользователь** (`user`) — добавляет ШУ, пользуется чатом поддержки, просматривает/запрашивает документацию, создаёт сервисные заявки.
2. **Оператор** (`operator`) — отвечает в чате, просматривает все данные. Создаётся через `POST /admin/users/operators` или CLI.
3. **Администратор** (`admin`) — полное управление данными, создаёт операторов. Создаётся суперадмином через API или CLI.
4. **Суперадмин** (`superadmin`) — управляет администраторами, имеет все права. Создаётся **только через CLI**.
5. **Бот** (`bot`) — системная роль для Аси, не отображается в списках пользователей.

> Иерархия: каждая роль выше автоматически имеет все права более низкой роли.

---

# Флоу для фронтенда

Здесь описаны последовательности вызовов API для каждого сценария приложения.

---

## 1. Авторизация

### Регистрация пользователя
```
1. POST /auth/register/start
   Тело: { phone, password, password_confirm, full_name, user_type, organization_name? }
   Ответ: { message, resend_after_seconds }
   → Показать экран ввода SMS-кода. Запустить таймер resend_after_seconds.

2. POST /auth/register/complete
   Тело: { phone, code }
   Ответ: { access_token, refresh_token, token_type }
   → Сохранить оба токена. Перейти в приложение.

   Если код не пришёл или истёк:
   POST /auth/register/resend
   Тело: { phone }
```

### Вход пользователя
```
POST /auth/login
Тело: { phone, password }
Ответ: { access_token, refresh_token }
→ Сохранить токены. Зарегистрировать FCM-токен устройства (см. Уведомления).
```

### Вход администратора / оператора
```
POST /auth/admin-login
Тело: { login, password }
Ответ: { access_token, refresh_token }
→ Сохранить токены.
```

### Использование токенов
```
Каждый запрос: заголовок Authorization: Bearer {access_token}

Если сервер вернул 401:
  POST /auth/refresh
  Тело: { refresh_token }
  Ответ: { access_token, refresh_token }
  → Сохранить новые токены, повторить исходный запрос.

  Если /auth/refresh тоже вернул 401 → разлогинить пользователя.
```

### Выход
```
POST /auth/logout
Тело: { refresh_token }
→ Удалить токены локально.
→ DELETE /device-tokens/{fcm_token} — чтобы перестали приходить push.
```

### Редактирование профиля
```
1. GET /auth/me → получить текущие данные, предзаполнить форму
2. Пользователь меняет поля
3. PATCH /auth/me
   Тело: { full_name?, email?, organization_name? }
   → Передавать только изменённые поля.
   Ответ: обновлённый профиль.
```

### Смена номера телефона
```
1. POST /auth/change-phone/start
   Тело: { new_phone: "+375291234568" }
   → SMS-код отправляется на НОВЫЙ номер.
   Ответ: { resend_after_seconds: 60 }

2. Пользователь вводит код из SMS
3. POST /auth/change-phone/complete
   Тело: { new_phone: "+375291234568", code: "123456" }
   → Номер изменён.
```

---

## 2. Шкафы управления (ШУ)

### Экран "Мои ШУ"
```
GET /cabinets
Ответ: список { cabinet_id, type, object_number, warranty_status,
                custom_name, unread_count, is_primary }
→ Показать карточки. unread_count > 0 — показать индикатор.
→ warranty_status: "active" | "expiring_soon" | "expired" — цвет индикатора.
→ custom_name — название. Если null, показывается admin_internal_name или object_number.
```

### Добавление ШУ через QR
```
1. Пользователь сканирует QR
2. POST /cabinets/add-by-qr
   Тело: { qr_data: "savt://cabinet/A3F7BC12..." }
   Ответ: { status, message }
   
   status = "linked" → ШУ сразу добавлен, обновить список.
   status = "request_submitted" → заявка отправлена, ждать одобрения.
```

### Добавление ШУ через фото
```
1. POST /upload/attachment (multipart, file = фото наклейки)
   Ответ: { url: "/static/photos/abc.jpg" }

2. POST /cabinets/add-by-photo
   Тело: { photo_url: "/static/photos/abc.jpg", user_comment?: "..." }
   Ответ: { request_id, message }
   → Показать сообщение "Заявка отправлена".
```

### Карточка ШУ
```
GET /cabinets/{cabinet_id}
Ответ: { cabinet_id, type, object_number, description, purpose,
         warranty_starts_at, warranty_ends_at, warranty_status,
         custom_name, custom_comment, is_primary }
```

### Редактирование названия / комментария
```
1. GET /cabinets/{cabinet_id} → предзаполнить поля
2. PATCH /cabinets/{cabinet_id}
   Тело: { custom_name?: "Мой шкаф", custom_comment?: "Заметка" }
   → Передавать только изменённые поля. null = сброс к значению по умолчанию.
```

### Документы ШУ
```
GET /cabinets/{cabinet_id}/documents
Параметры: tag_ids[], doc_type, sort_by, sort_order, page, size
Ответ: { items: [{ id, title, doc_type, file_url, has_access, tags }], total, ... }

→ has_access = true → показать кнопку "Скачать" → GET /documents/{id}/download
→ has_access = false → показать кнопку "Запросить доступ" →
    POST /documents/{id}/request-access
    Тело: { user_message?: "Нужен для проверки" }
```

### Фото ШУ
```
GET /cabinets/{cabinet_id}/photos
Ответ: { items: [{ id, url, caption, sort_order }], ... }
→ Показать галерею.
```

### Чат ШУ
```
GET /cabinets/{cabinet_id}/chat
Ответ: { id, chat_type: "cabinet", problem_status, bot_active }
→ Сохранить chat_id для дальнейших запросов.
→ Перейти в экран чата.
```

---

## 3. Чат

### Открытие чата
```
GET /chats
Ответ: список всех чатов { id, chat_type, cabinet_name, last_message_text,
                           unread_count, problem_status, bot_active }
→ cabinet: чат по конкретному ШУ
→ support: общий чат поддержки
→ notes: личные заметки пользователя
```

### История сообщений (бесконечный скролл вверх)
```
Первая загрузка:
GET /chats/{chat_id}/messages?limit=30
Ответ: массив сообщений от новых к старым.
→ Отобразить снизу вверх.

Загрузка более старых (пользователь скроллит вверх):
GET /chats/{chat_id}/messages?before_id={oldest_msg_id}&limit=30
→ Добавить в начало списка.

Загрузка вокруг конкретного сообщения (поиск + переход по ссылке):
GET /chats/{chat_id}/messages?around_id={msg_id}&limit=30
→ Возвращает сообщения до и после указанного ID.
```

### Отправка сообщения
```
Только текст:
POST /chats/{chat_id}/messages
Тело: { text: "Сообщение" }

С вложением:
1. POST /upload/attachment (multipart, file)
   Ответ: { url, ... }

2. POST /chats/{chat_id}/messages
   Тело: {
     text: "Смотри файл",
     attachments: [{
       file_url: "/static/documents/abc.pdf",
       file_name: "abc.pdf",
       file_size_bytes: 204800,
       mime_type: "application/pdf"
     }]
   }

Голосовое:
1. POST /upload/voice (multipart, file)
2. POST /chats/{chat_id}/messages
   Тело: {
     attachments: [{
       file_url: "...", file_name: "voice.ogg",
       file_size_bytes: 512, mime_type: "audio/ogg",
       duration_seconds: 15
     }]
   }
```

### Отметить прочитанным
```
POST /chats/{chat_id}/read
→ Вызывать когда пользователь открыл чат или прочитал последнее сообщение.
→ Сбрасывает unread_count.
```

### Реакции
```
Поставить: POST /chats/{chat_id}/messages/{msg_id}/reactions/{emoji}
Убрать:    DELETE /chats/{chat_id}/messages/{msg_id}/reactions/{emoji}
```

### Закреплённые сообщения (несколько на чат, до 10)
```
Список:            GET    /chats/{chat_id}/pinned
Закрепить:         PUT    /chats/{chat_id}/pin/{msg_id}   (идемпотентно)
Открепить одно:    DELETE /chats/{chat_id}/pin/{msg_id}
Открепить все:     DELETE /chats/{chat_id}/pin
→ Все 4 ответа: list[MessageOut], от новых к старым по времени закрепления
```

### Обои чата
Обои — личная настройка пользователя; собеседник не видит выбранный фон.
```
Установить: PATCH /chats/{chat_id}/wallpaper
Тело: { "wallpaper_url": "/static/photos/bg.jpg" }
Сбросить:   { "wallpaper_url": null }
→ Ответ: ChatSettingsOut с обновлённым wallpaper_url
   (обои сохраняются в настройках пользователя, а не в самом чате)
```

### Удалить чат
```
DELETE /chats/{chat_id}          — пользователь (только cabinet и notes; поддержку удалить нельзя)
DELETE /operator/chats/{chat_id} — оператор/админ (любой чат)
→ 204 No Content
```

### Очистить историю сообщений (только оператор)
```
DELETE /operator/chats/{chat_id}/messages
→ Soft-delete всех сообщений (тексты обнуляются)
→ 204 No Content
```

### Настройки вида чата (цвета, шрифт, обои)
Все настройки вида — личные: собеседник видит чат со своими настройками.

Глобальные настройки (применяются ко всем чатам пользователя):
```
GET   /chats/settings          → ChatSettingsOut
PATCH /chats/settings          → ChatSettingsOut
```

Per-chat override (приоритет над глобальными):
```
GET    /chats/{chat_id}/settings   → возвращает per-chat если есть, иначе global
PATCH  /chats/{chat_id}/settings   → создаёт/обновляет override для конкретного чата
DELETE /chats/{chat_id}/settings   → сбрасывает override (откат к глобальным)
→ 204 No Content для DELETE
```

Обои — часть настроек per-chat; можно задать через `PATCH /chats/{chat_id}/wallpaper`
или через `PATCH /chats/{chat_id}/settings` (поле `wallpaper_url`).

Тело запроса `ChatSettingsIn` (все поля опциональны):
```json
{
  "own_bubble_color": "#DCF8C6",
  "other_bubble_color": "#FFFFFF",
  "bot_bubble_color": "#E8E8E8",
  "own_text_color": "#000000",
  "other_text_color": "#000000",
  "bot_text_color": "#555555",
  "nick_color": "#128C7E",
  "font_size": 14,
  "wallpaper_url": "/static/photos/bg.jpg"
}
```
Цвета — HEX `#RRGGBB`. `font_size` — от 8 до 24. `wallpaper_url` — URL обоев (null = сброс).

### Голосовое сообщение → текст
```
1. Загрузить: POST /upload/voice (multipart, file)
   Форматы: ogg, mp3, m4a, wav, webm, aac
   Ответ: { url: "/static/voices/abc.ogg" }

2. Распознать: POST /upload/transcribe
   Тело: { "file_url": "/static/voices/abc.ogg" }
   Ответ: { "text": "распознанный текст" }
```

---

## 4. Уведомления

### При старте приложения
```
1. GET /notifications?is_read=false&size=50
   → Показать счётчик непрочитанных.

2. POST /device-tokens
   Тело: { token: "{fcm_token}", platform: "android" | "ios" }
   → Зарегистрировать для push-уведомлений.
```

### Экран уведомлений
```
GET /notifications?page=1&size=20
→ Список всех уведомлений.

POST /notifications/{id}/read   → прочитать одно
POST /notifications/read-all    → прочитать все
```

### Настройки уведомлений
```
1. GET /notifications/settings → предзаполнить переключатели
2. PATCH /notifications/settings
   Тело: { promotional: false }  ← только изменённые поля
```

### Push при новом сообщении
```
Когда оператор или бот пишет пользователю — сервер автоматически
отправляет FCM push на все зарегистрированные устройства пользователя:
  title: имя отправителя ("Ася" / "Оператор Иванов")
  body:  текст сообщения (до 100 символов)
  data:  { chat_id: "3", type: "chat_message" }

Условие: тип уведомления chat_message должен быть включён
в настройках пользователя (по умолчанию — включён).
```

---

## 5. Сервисные заявки

### Создание заявки
```
POST /service-requests
Тело: {
  cabinet_id: 5,
  request_type: "repair",   ← repair | maintenance | inspection | other
  description: "Не работает кнопка управления (минимум 10 символов)"
}
Ответ: созданная заявка со статусом "open"
```

### Мои заявки
```
GET /service-requests?status=open&page=1
→ Список своих заявок с фильтром по статусу.
Статусы: open → in_progress → closed
```

---

## 6. База знаний

### Навигация
```
1. GET /kb/categories
   Ответ: плоский список категорий с parent_id.
   → Построить дерево на фронте по parent_id.

2. GET /kb/articles?category_id=1&page=1
   Ответ: { items: [{ id, title, description, attachment_count, tags }], ... }
   → Показать список записей в категории.

3. GET /kb/articles/{id}
   Ответ: { ..., attachments: [{ id, file_url, doc_type, title, ... }] }
   → Показать детали с кнопками скачивания.
```

### Скачивание файла из KB
```
GET /kb/articles/{article_id}/attachments/{att_id}/download
→ Открыть файл или скачать.
```

### Избранное
```
Добавить: POST /favorites { "entity_type": "kb_article", "entity_id": 3 }
Убрать:   DELETE /favorites/kb_article/3
Список:   GET /favorites?entity_type=kb_article
```

### Поиск по тегам
```
GET /kb/articles?tag_ids=1&tag_ids=2
→ Вернёт записи у которых есть хотя бы один из указанных тегов.
```

---

## 7. Загрузка файлов

### Общее правило
```
Любой файл сначала загружается, потом URL передаётся в нужный эндпоинт.

Вложение (фото/документ/видео):
POST /upload/attachment (multipart/form-data, поле: file)
Ответ: { url: "/static/photos/abc.jpg" }

Голосовое сообщение:
POST /upload/voice (multipart/form-data, поле: file)
Ответ: { url: "/static/voices/abc.ogg" }

Голосовое → текст:
POST /upload/transcribe (JSON: { file_url: "/static/voices/abc.ogg" })
Ответ: { text: "распознанный текст" }
```

---

## 8. PATCH-запросы — как редактировать

### Правило для всех PATCH-эндпоинтов
```
1. Сначала GET → получить текущие данные → предзаполнить форму
2. Пользователь меняет нужные поля
3. PATCH → отправить ТОЛЬКО изменённые поля

Если поле не отправить → оно не изменится.
Если отправить null → значение сбросится (если поле nullable).

Пример редактирования ШУ:
1. GET /cabinets/{id}           → { custom_name: "Старое название", custom_comment: null }
2. PATCH /cabinets/{id}         → { custom_name: "Новое название" }
   ← custom_comment не трогали → не меняется
```

---

## Рут `auth` — авторизация и аккаунт

### POST `/auth/register/start`
Начало регистрации. Пользователь вводит данные, на телефон отправляется SMS-код.
```json
{
  "phone": "+375291234567",
  "password": "minLength8",
  "password_confirm": "minLength8",
  "full_name": "Иванов Иван Иванович",
  "user_type": "individual",
  "organization_name": null
}
```
- `phone` — номер в международном формате, проверяется на корректность
- `password` — минимум 8 символов
- `password_confirm` — должен совпадать с `password`
- `user_type` — `individual` или `organization`
- `organization_name` — обязателен если `user_type = organization`

Ответ:
```json
{
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 60
}
```
- `resend_after_seconds` — кулдаун в секундах до возможности повторно запросить код

---

### POST `/auth/register/complete`
Подтверждение телефона кодом из SMS.
```json
{
  "phone": "+375291234567",
  "code": "123456"
}
```
Ответ: `201 Created`
```json
{
  "access_token": "eyJ...",
  "refresh_token": "abc123...",
  "token_type": "bearer"
}
```

---

### POST `/auth/register/resend`
Повторная отправка кода (если не пришёл или истёк).
```json
{
  "phone": "+375291234567"
}
```
Ответ аналогичен `/register/start`.

---

### POST `/auth/login`
Вход пользователя по телефону и паролю.
```json
{
  "phone": "+375291234567",
  "password": "myPassword"
}
```
Ответ:
```json
{
  "access_token": "eyJ...",
  "refresh_token": "abc123...",
  "token_type": "bearer"
}
```

---

### POST `/auth/refresh`
Обновление access-токена по refresh-токену.
```json
{
  "refresh_token": "abc123..."
}
```
Ответ: новая пара токенов (аналогично `/login`).

---

### POST `/auth/logout`
Выход — инвалидирует refresh-токен.
```json
{
  "refresh_token": "abc123..."
}
```
Ответ: `204 No Content`

---

### GET `/auth/me`
Данные текущего авторизованного пользователя (требует Bearer-токен).
```json
{
  "id": 1,
  "phone": "+375291234567",
  "email": null,
  "user_type": "individual",
  "organization_name": null,
  "full_name": "Иванов Иван",
  "role": "user",
  "is_phone_verified": true,
  "is_verified": false
}
```
- `is_verified` — подтверждён ли аккаунт администратором

---

### DELETE `/auth/me`
Удаление собственного аккаунта (доступно всем авторизованным пользователям). Отзывает все refresh-токены и удаляет аккаунт безвозвратно.

Ответ: `204 No Content`.

---

### POST `/auth/password-reset/start`
Запрос SMS-кода для сброса пароля.
```json
{ "phone": "+375291234567" }
```
Ответ:
```json
{
  "message": "На телефон отправлен код",
  "resend_after_seconds": 60
}
```

---

### POST `/auth/password-reset/complete`
Установка нового пароля после подтверждения кода.
```json
{
  "phone": "+375291234567",
  "code": "123456",
  "new_password": "newPassword8",
  "new_password_confirm": "newPassword8"
}
```
Ответ: `204 No Content`. Все сессии инвалидируются.

---

### POST `/auth/password-change`
Смена пароля для авторизованного пользователя (требует Bearer-токен).
```json
{
  "password": "oldPassword",
  "new_password": "newPassword8",
  "new_password_confirm": "newPassword8"
}
```
Ответ: `200 OK` с сообщением об успехе.

---

### PATCH `/auth/me`
Редактирование личных данных авторизованного пользователя. Передавать только изменённые поля.
```json
{
  "full_name": "Иванов Иван Иванович",
  "email": "ivan@example.com",
  "organization_name": "ООО Ромашка"
}
```
Ответ: обновлённый профиль (аналогично `GET /auth/me`).

---

### POST `/auth/change-phone/start`
Запрос SMS-кода для смены номера телефона. Код отправляется на **новый** номер. Лимит — 5 запросов в минуту.
```json
{ "new_phone": "+375291234568" }
```
Ответ: `{ "message": "...", "resend_after_seconds": 60 }`.

Ошибки:
- `409` — новый номер уже используется другим пользователем
- `409` — это уже ваш текущий номер

---

### POST `/auth/change-phone/complete`
Подтверждение смены номера кодом из SMS. Лимит — 10 запросов в минуту.
```json
{
  "new_phone": "+375291234568",
  "code": "123456"
}
```
Ответ: `204 No Content`. Номер изменён.

---

### POST `/auth/admin-login`
Вход для администратора и оператора — по логину, не по телефону.
```json
{
  "login": "operator1",
  "password": "P@ssword8"
}
```
Ответ: пара токенов (`access_token`, `refresh_token`). Доступен только для ролей `admin` и `operator` — обычный пользователь получит `401`.

---

## Рут `upload` — загрузка файлов

Все эндпоинты требуют Bearer-токен. Принимают `multipart/form-data`.

### POST `/upload/attachment`
Загрузка вложения. Принимает **любой MIME-тип** — лимитов по формату нет. Лимит размера — 500 МБ.

Директория сохранения определяется по MIME-типу:
- `image/*` → `/static/photos/`
- `video/*` → `/static/videos/`
- `audio/*` → `/static/voices/`
- всё остальное → `/static/files/`

Ответ:
```json
{ "url": "/static/photos/abc123.jpg" }
```

Ошибки:
- `413` — файл превышает 500 МБ

---

### POST `/upload/voice`
Загрузка голосового сообщения.

| Форматы | Лимит |
|---|---|
| ogg, mp3, m4a, wav, webm, aac | 25 МБ |

`audio/webm` и `video/webm` (Chrome/Firefox запись) поддерживаются напрямую.

Ответ:
```json
{ "url": "/static/voices/abc123.ogg" }
```

Ошибки:
- `415` — неподдерживаемый MIME-тип файла
- `413` — файл превышает лимит размера

---

### GET `/upload/download?url={file_url}`
Скачать любой файл (фото, документ, видео, голосовое) с заголовком `Content-Disposition: attachment`. Требует Bearer-токен.

Параметр `url` — значение поля `file_url` из вложения или `url` из фото, например `/static/photos/abc123.jpg`.

- `400` — URL не указывает на загруженный файл
- `404` — файл не найден на диске

---

### POST `/upload/transcribe`
Распознать голосовое сообщение в текст через Yandex SpeechKit. Требует настроенных `YANDEX_FOLDER_ID` и `YANDEX_API_KEY`.

Тело запроса (JSON):
```json
{ "file_url": "/static/voices/abc123.ogg" }
```

Ответ:
```json
{ "text": "распознанный текст сообщения" }
```

Принимает любой формат, который умеет читать `ffmpeg` (`ogg/opus`, `webm`, `mp3`, `m4a`, `aac`, `wav` и т.д.) — сервер сам перекодирует файл в OGG/Opus перед отправкой в Yandex, поэтому реальный контейнер/кодек присланного файла значения не имеет. Файл должен быть предварительно загружен через `POST /upload/voice`.

**Размер файла (после перекодирования) обрабатывается гибридно:**
- **≤ 1 МБ** (примерно 30-60 сек записи) — синхронное распознавание (`stt:recognize`), ответ обычно за 1-3 секунды.
- **> 1 МБ** — асинхронное распознавание (`longRunningRecognize`): файл временно загружается в Yandex Object Storage (бакет приватный, доступ для Yandex через presigned URL на 1 час), сервер сам дожидается результата (поллинг, до ~100 сек) и удаляет файл из Object Storage по завершении. Для фронта это прозрачно — ответ остаётся `{ text }`, просто запрос может идти дольше. Требует дополнительно настроенных `YANDEX_STORAGE_BUCKET`, `YANDEX_STORAGE_ACCESS_KEY_ID`, `YANDEX_STORAGE_SECRET_ACCESS_KEY`.

Эндпоинт **stateless**: ничего не сохраняет в БД, не привязан к чату/сообщению — принимает любой `file_url` из `/static/voices/...` и просто возвращает текст. Повторный вызов — повторное распознавание.

Ошибки:
- `400` — некорректный URL, либо файл не распознан как аудио (ffmpeg не смог его обработать)
- `404` — файл не найден
- `503` — Yandex SpeechKit/Object Storage недоступны, не настроены, вернули ошибку, либо распознавание не уложилось в таймаут (для очень длинных записей)

---

## Рут `admin: cabinets` — управление ШУ (только админ)

### POST `/admin/cabinets`
Создание нового ШУ. `unique_code` генерируется автоматически (64-бит случайный код).
```json
{
  "type": "вентиляционная установка",
  "object_number": "29_099",
  "description": "Описание",
  "warranty_starts_at": "2025-01-01T00:00:00Z",
  "warranty_ends_at": "2027-01-01T00:00:00Z",
  "admin_internal_name": "ШУ-18К",
  "admin_comment": "Комментарий для внутреннего использования",
  "purpose": "Вентиляция",
  "latitude": 53.9045,
  "longitude": 27.5615
}
```
`latitude` (-90…90) и `longitude` (-180…180) — необязательны, геолокация ШУ на карте.

**Поле `type`:**
- Приводится к нижнему регистру автоматически (`"Вентиляция"` = `"вентиляция"`)
- Если тип новый — создаётся тег `scope="cabinet_type"` автоматически
- На фронте: получить список готовых типов через `GET /tags?scope=cabinet_type` и показать выпадающий список

Ответ — полная информация о созданном ШУ включая `unique_code` и `tags`.

---

### GET `/admin/cabinets`
Список всех ШУ. Доступно оператору и администратору. Параметры:
- `search` — поиск по типу, номеру объекта, названию, назначению, описанию, комментарию
- `tag_ids` — фильтр по тегам (`?tag_ids=1&tag_ids=2`)
- `has_documents` — `true` / `false` — есть ли документы
- `has_photos` — `true` / `false` — есть ли фото
- `has_users` — `true` / `false` — есть ли привязанный пользователь
- `has_service_requests` — `true` / `false` — есть ли сервисные заявки
- `warranty_status` — `active` (гарантия действует) / `expired` (истекла) / `none` (не указана)
- `sort_by` — `type`, `warranty_ends_at`, `object_number`, `admin_internal_name`, `purpose`, `created_at`
- `sort_order` — `asc`, `desc`
- `page`, `size` — пагинация (по умолч. `1` / `20`, максимум `100`)

Каждый элемент содержит:
```json
{
  "id": 1,
  "unique_code": "A3F7BC1254E8D9F0",
  "type": "Вентиляционная установка",
  "object_number": "29_099",
  "purpose": "Вентиляция",
  "warranty_starts_at": "2025-01-01T00:00:00Z",
  "warranty_ends_at": "2027-01-01T00:00:00Z",
  "warranty_status": "active",
  "admin_internal_name": "ШУ-18К",
  "admin_comment": "Внутренний комментарий",
  "tags": [{ "id": 2, "name": "Электрика", "scope": "cabinet" }],
  "created_at": "2026-05-01T10:00:00Z"
}
```
`warranty_status`: `active`, `expiring_soon` (≤30 дней), `expired`.

---

### GET `/admin/cabinets/geo`
Лёгкий endpoint для карты — возвращает **все** ШУ одним запросом без пагинации.

Ответ (`list[CabinetGeoItem]`):
```json
[
  {
    "id": 3,
    "object_number": "29_099",
    "admin_internal_name": "Главная подстанция",
    "warranty_status": "active",
    "latitude": 53.9,
    "longitude": 27.56,
    "has_open_requests": true
  }
]
```

`warranty_status`: `active` | `expiring_soon` | `expired`.  
`has_open_requests`: есть ли хотя бы одна сервисная заявка со статусом `open`.  
ШУ без координат тоже включены (`latitude`/`longitude` = `null`) — фронт фильтрует сам.

---

### GET `/admin/cabinets/{cabinet_id}`
Детальная информация о ШУ с тегами.

---

### GET `/admin/cabinets/{cabinet_id}/qr`
Генерирует QR-код для ШУ в формате PNG (с логотипом SAVT если есть файл `app/assets/savt_logo.png`).

QR кодирует строку: `savt://cabinet/{unique_code}`

Ответ: бинарный PNG (`image/png`).

---

### PATCH `/admin/cabinets/{cabinet_id}`
Обновление данных ШУ (все поля опциональны, в т.ч. `latitude` и `longitude`). Возвращает обновлённый ШУ с тегами.

---

### PUT `/admin/cabinets/{cabinet_id}/tags`
Привязать теги к ШУ (полная замена). Только для администратора.
```json
{ "tag_ids": [1, 2] }
```
Пустой список снимает все теги. `204 No Content`.

> Рекомендуется привязывать теги со `scope="cabinet"` (на фронте — выбирать из `GET /tags?scope=cabinet`), но сервер не проверяет `scope` тега при сохранении.

---

### DELETE `/admin/cabinets/{cabinet_id}`
Удаление ШУ. `204 No Content`.

**Soft-delete**: запись не стирается из БД, а помечается `deleted_at`. Удалённый ШУ:
- пропадает из `GET /admin/cabinets`, `GET /admin/cabinets/geo` и общего поиска;
- пользователь не может привязать его заново по QR-коду (`POST /cabinets/add-by-qr` вернёт 404) — его `unique_code` больше не может быть переиспользован новым ШУ;
- нельзя привязать через одобрение заявок (`POST /admin/cabinet-requests/additions/{id}/approve` и `.../shares/{id}/approve` вернут 404, даже если заявка была создана до удаления);
- не участвует в проверке истечения гарантии (warranty-уведомления не шлются).

Уже существующие привязки пользователей, чаты и заявки на обслуживание по удалённому ШУ не затрагиваются — данные и история сохраняются.

---

### GET `/admin/cabinets/{cabinet_id}/users`
Список пользователей, привязанных к ШУ. Доступно оператору и админу.
```json
[
  {
    "user_id": 1,
    "full_name": "Иванов Иван",
    "phone": "+375291234567",
    "user_type": "individual",
    "is_primary": true,
    "custom_name": "Мой шкаф",
    "added_at": "2026-05-12T10:00:00Z"
  }
]
```

---

### DELETE `/admin/cabinets/{cabinet_id}/users/{user_id}`
Отвязать пользователя от ШУ с указанием причины. Только для админа. Логируется в `audit_log`.
```json
{ "reason": "Причина отвязки" }
```
Ответ: `204 No Content`.

---

## Рут `admin: cabinet requests` — заявки по ШУ (просмотр — оператор/админ, одобрение/отклонение — только админ)

### GET `/admin/cabinet-requests/additions`
Заявки на добавление ШУ через фото. Параметры:
- `status` — `pending` / `approved` / `rejected`
- `resolved_by_admin_id` — заявки, обработанные конкретным администратором
- `search` — поиск по ФИО, телефону, организации пользователя, комментарию пользователя и ответу администратора
- `sort_by` — `created_at` (по умолч.), `resolved_at`, `status`, `user_full_name`
- `sort_order` — `asc` / `desc`
- `page`, `size`

```json
{
  "items": [
    {
      "id": 1,
      "user_id": 8,
      "user_full_name": "Иванов Иван",
      "user_phone": "+375291234567",
      "user_type": "individual",
      "organization_name": null,
      "user_is_verified": false,
      "user_registered_at": "2026-04-01T10:00:00Z",
      "photo_url": "/static/photos/abc.jpg",
      "user_comment": "Шкаф на заводе",
      "status": "pending",
      "cabinet_id": null,
      "admin_response": null,
      "resolved_by_admin_id": null,
      "created_at": "2026-05-12T08:00:00Z",
      "resolved_at": null
    }
  ],
  "total": 1, "page": 1, "size": 20, "pages": 1
}
```
`cabinet_id` — `null` пока заявка не одобрена.

---

### POST `/admin/cabinet-requests/additions/{request_id}/approve`
Одобрение заявки. Администратор предварительно создаёт ШУ, затем указывает его ID.
```json
{
  "cabinet_id": 5,
  "admin_response": "Шкаф добавлен"
}
```
Ответ: `204 No Content`. Создаётся `UserCabinet` с `is_primary=true`.

---

### POST `/admin/cabinet-requests/additions/{request_id}/reject`
Отклонение заявки.
```json
{ "admin_response": "Фото нечёткое, повторите попытку" }
```
Ответ: `204 No Content`.

---

### GET `/admin/cabinet-requests/shares`
Заявки на доступ к уже существующему ШУ (сканирован QR, но ШУ уже занят). Параметры:
- `status` — `pending` / `approved` / `rejected`
- `resolved_by_admin_id` — заявки, обработанные конкретным администратором
- `search` — поиск по ФИО/телефону пользователя, типу/номеру/названию ШУ, комментарию пользователя и ответу администратора
- `sort_by` — `created_at` (по умолч.), `resolved_at`, `status`, `user_full_name`, `cabinet_object_number`
- `sort_order` — `asc` / `desc`
- `page`, `size`

```json
{
  "items": [
    {
      "id": 1,
      "user_id": 10,
      "user_full_name": "Петров Пётр",
      "user_phone": "+375291111111",
      "user_type": "organization",
      "organization_name": "ООО Ромашка",
      "user_is_verified": true,
      "user_registered_at": "2026-03-15T08:00:00Z",
      "cabinet_id": 5,
      "cabinet_type": "Вентиляционная установка",
      "cabinet_object_number": "29_099",
      "user_comment": null,
      "status": "pending",
      "admin_response": null,
      "resolved_by_admin_id": null,
      "created_at": "2026-05-12T09:00:00Z",
      "resolved_at": null
    }
  ],
  "total": 1, "page": 1, "size": 20, "pages": 1
}
```

---

### POST `/admin/cabinet-requests/shares/{request_id}/approve`
Одобрение доступа.
```json
{ "admin_response": "Доступ предоставлен" }
```
Ответ: `204 No Content`. Создаётся `UserCabinet` с `is_primary=false`.

---

### POST `/admin/cabinet-requests/shares/{request_id}/reject`
Отклонение доступа.
```json
{ "admin_response": "Причина отказа" }
```
Ответ: `204 No Content`.

---

## Рут `admin: users` — управление пользователями (админ/оператор)

> Список **не показывает** пользователей с ролями `admin` и `bot` (системные аккаунты). Только `user` и `operator`.

### POST `/admin/users/admins`
Создать администратора. Только для **суперадмина**. Логируется в `audit_log`.
```json
{
  "login": "admin2",
  "password": "securePass8",
  "full_name": "Сидоров Сидор"
}
```
Ответ: созданный пользователь (`AdminUserListOut`), `201 Created`.

---

### POST `/admin/users/operators`
Создать оператора. Только для администратора. Логируется в `audit_log`.
```json
{
  "login": "operator2",
  "password": "securePass8",
  "full_name": "Иванов Иван"
}
```
- `login` — минимум 3 символа, без пробелов, приводится к нижнему регистру, должен быть уникальным
- `password` — минимум 8 символов
- `full_name` — необязателен

Ответ: созданный пользователь (`AdminUserListOut`), `201 Created`.

> Создание **администратора** — через API (только суперадмин) или CLI:
> ```bash
> docker exec savt-backend-api-1 python -m app.cli create-admin <login> <password> [full_name]
> ```

---

### DELETE `/admin/users/operators/{user_id}`
Удалить оператора. Только для администратора. Логируется в `audit_log`.

Что происходит:
- Все сессии оператора немедленно отзываются (принудительный logout)
- Аккаунт деактивируется и анонимизируется
- Переписка в чатах сохраняется
- Оператор исчезает из всех списков

Ответ: `204 No Content`.

---

### GET `/admin/users`
Только пользователи (`role=user`). Параметры:
- `search` — поиск по ФИО, телефону, логину, email, организации
- `is_active` — `true` / `false`
- `is_verified` — `true` / `false` — верификация аккаунта администратором
- `is_phone_verified` — `true` / `false` — подтверждённый номер телефона
- `user_type` — `individual` / `organization`
- `sort_by` — `created_at` (по умолч.), `full_name`, `phone`, `email`, `login`, `organization_name`, `role`
- `sort_order` — `asc` / `desc`
- `page`, `size` — пагинация (по умолч. `page=1`, `size=20`, максимум `100`)

---

### GET `/admin/operators`
Только операторы (`role=operator`). Параметры: аналогично `/admin/users` (без значения `role` в `sort_by`), включая `is_verified` и `is_phone_verified`.

---

### GET `/admin/admins`
Только администраторы (`role=admin`). **Только для суперадмина.** Параметры: аналогично `/admin/users` (без значения `role` в `sort_by`), включая `is_verified` и `is_phone_verified`.

### GET `/admin/admins/{user_id}`
Детальная информация об администраторе. **Только для суперадмина.**

---

### POST `/admin/admins`
Создать администратора (короткий alias для `POST /admin/users/admins`). **Только для суперадмина.**

---

Все три эндпоинта возвращают `PageOut[AdminUserListOut]`:
```json
{
  "items": [
    {
      "id": 1,
      "phone": "+375291234567",
      "login": null,
      "full_name": "Иванов Иван",
      "user_type": "individual",
      "organization_name": null,
      "role": "user",
      "is_active": true,
      "is_phone_verified": true,
      "is_verified": false,
      "created_at": "2026-05-01T10:00:00Z"
    }
  ],
  "total": 50, "page": 1, "size": 20, "pages": 3
}
```

---

### GET `/admin/users/{user_id}`
Детальная информация о пользователе включая список его ШУ с гарантийным статусом. Доступно только для пользователей с ролью `user`/`operator` — для администраторов, суперадминов и системных аккаунтов возвращает `404`.

```json
{
  "id": 1,
  "phone": "+375291234567",
  "login": null,
  "full_name": "Иванов Иван",
  "email": null,
  "user_type": "individual",
  "organization_name": null,
  "role": "user",
  "is_active": true,
  "is_phone_verified": true,
  "is_verified": false,
  "created_at": "2026-05-01T10:00:00Z",
  "cabinets": [
    {
      "cabinet_id": 5,
      "type": "Вентиляционная установка",
      "object_number": "29_099",
      "warranty_ends_at": "2027-01-01T00:00:00Z",
      "warranty_status": "active",
      "custom_name": "ШУ-18К",
      "is_primary": true,
      "added_at": "2026-05-12T10:00:00Z"
    }
  ]
}
```
`warranty_status`: `active`, `expiring_soon` (≤30 дней), `expired`.

---

### POST `/admin/users/{user_id}/verify`
Подтвердить аккаунт пользователя (`is_verified = true`). Только для администратора. Логируется в `audit_log`.

Ответ: `204 No Content`.

---

### POST `/admin/users/{user_id}/unverify`
Снять подтверждение аккаунта (`is_verified = false`). Только для администратора. Логируется в `audit_log`.

Ответ: `204 No Content`.

---

### POST `/admin/users/{user_id}/ban`
Блокировка пользователя (`is_active = false`). Только для администратора. Логируется в `audit_log`.
```json
{ "reason": "Нарушение условий использования" }
```
Поле `reason` обязательно (минимум 1 символ). Ответ: `204 No Content`.

---

### POST `/admin/users/{user_id}/unban`
Разблокировка пользователя. Только для администратора. Логируется в `audit_log`.

Ответ: `204 No Content`.

---

`verify`/`unverify`/`ban`/`unban` применимы только к пользователям с ролью `user`/`operator`. При попытке выполнить действие над администратором, суперадмином или системным аккаунтом — `403`.

---

## Рут `cabinets` — ШУ пользователя

### POST `/cabinets/add-by-qr`
Привязка ШУ через QR-код. Приложение сканирует QR и передаёт полное содержимое.
```json
{ "qr_data": "savt://cabinet/A3F7BC1254E8D9F0" }
```

Логика:
- ШУ не найден → `404`
- Уже привязан → `409`
- Нет первичного владельца → мгновенная привязка, `status: "linked"`
- Есть владелец → заявка на доступ, `status: "request_submitted"`
- Заявка уже есть → `409`

Ответ:
```json
{
  "status": "linked",
  "message": "ШУ успешно привязан"
}
```

---

### POST `/cabinets/add-by-photo`
Заявка на добавление ШУ через фото (предварительно загрузить через `/upload/attachment`).
```json
{
  "photo_url": "/static/photos/abc123.jpg",
  "user_comment": "Шкаф на заводе, цех 3"
}
```
Ответ:
```json
{
  "request_id": 1,
  "message": "Заявка отправлена на рассмотрение"
}
```

---

### GET `/cabinets`
Список ШУ текущего пользователя.
```json
[
  {
    "cabinet_id": 5,
    "type": "Вентиляционная установка",
    "object_number": "29_099",
    "warranty_ends_at": "2027-01-01T00:00:00Z",
    "warranty_status": "active",
    "custom_name": "ШУ-18К",
    "is_primary": true,
    "unread_count": 3
  }
]
```
- `custom_name` — пользовательское название; если не задано, возвращается `admin_internal_name`
- `unread_count` — количество непрочитанных сообщений в чате этого ШУ

---

### GET `/cabinets/{cabinet_id}`
Детальная информация о ШУ пользователя.
```json
{
  "cabinet_id": 5,
  "type": "Вентиляционная установка",
  "object_number": "29_099",
  "description": "Описание",
  "purpose": "Вентиляция",
  "warranty_starts_at": "2025-01-01T00:00:00Z",
  "warranty_ends_at": "2027-01-01T00:00:00Z",
  "warranty_status": "active",
  "latitude": 53.9045,
  "longitude": 27.5615,
  "custom_name": "Мой шкаф",
  "custom_comment": "Комментарий",
  "is_primary": true
}
```
`latitude`/`longitude` — `null` если геолокация не задана. Используется для отображения ШУ на карте.

---

### PATCH `/cabinets/{cabinet_id}`
Обновление пользовательского названия и комментария.
```json
{
  "custom_name": "Мой шкаф",
  "custom_comment": "Заметка"
}
```
Передача `null` сбрасывает значение. Возвращает обновлённую детальную карточку.

---

### DELETE `/cabinets/{cabinet_id}`
Открепить ШУ от аккаунта. `204 No Content`.

---

## Рут `qr` — генерация QR-кодов

### POST `/qr/generate`
Генерация QR-кода с произвольными данными. Доступно для админа и оператора.
```json
{ "data": "любой текст, URL, JSON-строка" }
```
Ответ: PNG-изображение (`image/png`). QR содержит логотип SAVT если файл `app/assets/savt_logo.png` существует.

---

## Рут `admin: documents` — документы и фото ШУ (админ/оператор)

Документы всегда привязаны к конкретному ШУ.

### POST `/admin/documents`
Загрузка документа к ШУ. `multipart/form-data`.

| Поле | Тип | Обязательно |
|---|---|---|
| `file` | файл | ✅ |
| `cabinet_id` | int | ✅ |
| `title` | string | нет (берётся имя файла) |
| `requires_approval` | bool | нет (по умолчанию `false`) |

`doc_type`, `mime_type`, `file_size_bytes` извлекаются автоматически.

---

### GET `/admin/documents`
Список документов. Параметры: `cabinet_id`, `doc_type`, `requires_approval`, `tag_ids`, `sort_by`, `sort_order`, `page`, `size`.

`sort_by`: `title`, `doc_type`, `file_size_bytes`, `created_at`.

---

### DELETE `/admin/documents/{doc_id}`
Удалить документ. Только админ. `204 No Content`.

---

### PUT `/admin/documents/{doc_id}/tags`
Привязать теги к документу (полная замена).
```json
{ "tag_ids": [1, 2, 3] }
```
Пустой список снимает все теги. `204 No Content`.

---

### POST `/admin/photos`
Загрузка фото к ШУ. `multipart/form-data`.

| Поле | Тип | Обязательно |
|---|---|---|
| `file` | файл (jpg/png/webp; сервер технически также принимает форматы из `/upload/attachment` — pdf/doc/docx/xls/xlsx/mp4/mov, но фронту следует загружать только изображения) | ✅ |
| `cabinet_id` | int | ✅ |
| `caption` | string | нет |
| `sort_order` | int | нет (по умолчанию `0`) |

---

### GET `/admin/photos`
Список фото. Параметры: `cabinet_id`, `page`, `size`.

---

### PATCH `/admin/photos/{photo_id}`
Изменить подпись и порядок фото.
```json
{ "caption": "Вид спереди", "sort_order": 1 }
```

---

### DELETE `/admin/photos/{photo_id}`
Удалить фото. Только админ. `204 No Content`.

---

### GET `/admin/document-requests`
Заявки пользователей на доступ к закрытым документам. Параметры:
- `status` — `pending` / `approved` / `rejected`
- `resolved_by_admin_id` — заявки, обработанные конкретным администратором
- `search` — поиск по ФИО/телефону/организации пользователя, типу документа, сообщению пользователя и ответу администратора
- `sort_by` — `created_at` (по умолч.), `resolved_at`, `status`, `user_full_name`, `doc_type`
- `sort_order` — `asc` / `desc`
- `page`, `size`

```json
{
  "items": [
    {
      "id": 3,
      "user_id": 8,
      "user_full_name": "Иванов Иван",
      "user_phone": "+375291234567",
      "user_type": "individual",
      "organization_name": null,
      "user_is_verified": true,
      "user_registered_at": "2025-11-01T08:30:00Z",
      "document_id": 12,
      "cabinet_id": 5,
      "doc_type": "passport",
      "status": "pending",
      "user_message": "Нужен для проверки",
      "admin_response": null,
      "resolved_by_admin_id": null,
      "created_at": "2026-06-01T09:00:00Z",
      "resolved_at": null
    }
  ],
  "total": 2, "page": 1, "size": 20, "pages": 1
}
```

---

### POST `/admin/document-requests/{request_id}/approve`
Одобрить заявку — пользователь получает доступ к документу.
```json
{ "admin_response": "Доступ предоставлен" }
```
`204 No Content`.

---

### POST `/admin/document-requests/{request_id}/reject`
Отклонить заявку.
```json
{ "admin_response": "Причина отказа" }
```
`204 No Content`.

---

## Рут `documents` — документы и фото для пользователя

### GET `/cabinets/{cabinet_id}/documents`
Список документов ШУ. Параметры: `tag_ids`, `doc_type`, `sort_by`, `sort_order`, `page`, `size`.

```json
{
  "items": [
    {
      "id": 1,
      "cabinet_id": 5,
      "title": "Паспорт ШУ-18К",
      "doc_type": "pdf",
      "file_url": "/static/documents/abc.pdf",
      "file_size_bytes": 204800,
      "mime_type": "application/pdf",
      "has_access": true,
      "tags": [{ "id": 1, "name": "паспорт" }]
    }
  ],
  "total": 10, "page": 1, "size": 20, "pages": 1
}
```
- `file_url` — `null` если `has_access=false` (документ закрыт, доступ не выдан)

---

### GET `/documents/{doc_id}/download`
Скачать / открыть файл. Проверяет доступ. Возвращает бинарный файл с правильным `Content-Type`.

- `403` — нет доступа
- `404` — документ не найден

---

### POST `/documents/{doc_id}/request-access`
Запросить доступ к закрытому документу.
```json
{ "user_message": "Нужен для проверки регламента" }
```
`user_message` необязателен. Ответ: `201 Created`, `{ "request_id": 1, "message": "Заявка отправлена" }`.

---

### GET `/cabinets/{cabinet_id}/photos`
Список фото ШУ. Параметры: `page`, `size`.

---

## Рут `tags` — теги

Теги разделены по области видимости (`scope`):
- `document` — теги для документов и статей базы знаний
- `cabinet` — теги для ШУ
- `cabinet_type` — типы ШУ (создаются автоматически при вводе нового типа)

### GET `/tags`
Список тегов. Параметры:
- `scope` — фильтр: `document` / `cabinet` / `cabinet_type`. Без фильтра — все теги.

```json
[
  { "id": 1, "name": "паспорт", "scope": "document" },
  { "id": 2, "name": "электрика", "scope": "cabinet_type" }
]
```

> Для автодополнения типа ШУ на фронте: `GET /tags?scope=cabinet_type`

---

### POST `/admin/tags`
Создать тег вручную. Доступно администратору и оператору.
```json
{ "name": "паспорт", "scope": "document" }
```
- `scope`: `document` / `cabinet` / `cabinet_type`
- Имя тега уникально в рамках одной области (сравнение без учёта регистра)

---

### DELETE `/admin/tags/{tag_id}`
Удалить тег. Только админ. `204 No Content`.

---

### PUT `/admin/kb-articles/{article_id}/tags`
Привязать теги к статье KB.
```json
{ "tag_ids": [1, 3] }
```
`204 No Content`.

---

## Рут `favorites` — избранное

### POST `/favorites`
Добавить в избранное.
```json
{ "entity_type": "document", "entity_id": 5 }
```
`entity_type`: `document`, `kb_article` или `faq_entry`. Ответ: объект избранного.

---

### DELETE `/favorites/{entity_type}/{entity_id}`
Удалить из избранного. `204 No Content`.

---

### GET `/favorites`
Список избранного. Параметры: `entity_type=document|kb_article|faq_entry`, `page`, `size`.
```json
{
  "items": [
    { "id": 1, "entity_type": "document", "entity_id": 5, "created_at": "..." }
  ],
  "total": 3, "page": 1, "size": 20, "pages": 1
}
```

---

## Рут `chats` — чаты

Три типа чатов создаются автоматически:
- `cabinet` — при привязке ШУ (по QR или одобрении заявки)
- `support` — при регистрации (общая поддержка с ботом)
- `notes` — при регистрации (личные заметки)

### GET `/chats`
Список всех чатов текущего пользователя.
```json
[
  {
    "id": 3,
    "chat_type": "cabinet",
    "cabinet_id": 5,
    "cabinet_name": "ШУ-18К",
    "cabinet_object_number": "29_099",
    "last_message_text": "Здравствуйте, помогите",
    "last_message_at": "2026-05-15T10:00:00Z",
    "unread_count": 2,
    "problem_status": "open",
    "bot_active": true,
    "operator_requested": false
  }
]
```

---

### GET `/cabinets/{cabinet_id}/chat`
Получить или создать чат для конкретного ШУ. Чат создаётся автоматически если его ещё нет.

> Требует, чтобы пользователь был привязан к ШУ (`user_cabinets`). Pending-заявка доступа не даёт — вернёт `403`.

---

### GET `/chats/{chat_id}/messages`
История сообщений. Параметры:
- `before_id` — ID сообщения, загрузить более старые (cursor pagination для бесконечного скролла)
- `around_id` — загрузить сообщения вокруг указанного ID (для перехода к конкретному сообщению)
- `limit` — количество (по умолчанию `30`, максимум `100`)
- `search` — поиск по тексту сообщений

Сообщения возвращаются от новых к старым.
```json
[
  {
    "id": 4,
    "chat_id": 3,
    "sender_id": 8,
    "sender_name": "Иванов Иван",
    "text": "Не работает кнопка",
    "reply_to_message_id": null,
    "is_read": false,
    "created_at": "2026-05-15T09:52:57Z",
    "edited_at": null,
    "deleted_at": null,
    "attachments": [],
    "reactions": []
  }
]
```

---

### POST `/chats/{chat_id}/messages`
Отправить сообщение. Вложения передаются как URL (предварительно загрузить через `/upload/attachment` или `/upload/voice`).
```json
{
  "text": "Текст сообщения",
  "reply_to_message_id": null,
  "attachments": [
    {
      "file_url": "/static/documents/abc.pdf",
      "file_name": "manual.pdf",
      "file_size_bytes": 204800,
      "mime_type": "application/pdf",
      "duration_seconds": null
    }
  ]
}
```
Либо текст, либо вложения — хотя бы одно обязательно.

---

### POST `/chats/{chat_id}/read`
Отметить все сообщения в чате как прочитанные. `204 No Content`.

---

### PATCH `/chats/{chat_id}/messages/{msg_id}`
Редактировать своё сообщение. Текст обязателен.

---

### DELETE `/chats/{chat_id}/messages/{msg_id}`
Мягкое удаление своего сообщения (текст очищается, `deleted_at` ставится). `204 No Content`.

---

### POST `/chats/{chat_id}/messages/{msg_id}/reactions/{emoji}`
Поставить реакцию на сообщение. `204 No Content`.

---

### DELETE `/chats/{chat_id}/messages/{msg_id}/reactions/{emoji}`
Убрать реакцию. `204 No Content`.

---

### DELETE `/chats/{chat_id}`
Удалить чат. Доступно только владельцу. Чат `support` удалить нельзя (403). Каскадно удаляет все сообщения. `204 No Content`.

---

### PATCH `/chats/{chat_id}/wallpaper`
Установить или сбросить обои чата. Доступно только владельцу. Обои личные — собеседник их не видит.
```json
{ "wallpaper_url": "/static/photos/bg.jpg" }
```
Для сброса: `{ "wallpaper_url": null }`. Ответ: `ChatSettingsOut`.

---

### GET `/chats/{chat_id}/pinned`
Список закреплённых сообщений чата. Сортировка: от недавно закреплённых к старым. Ответ: `list[MessageOut]`, пустой массив — ничего не закреплено.

---

### PUT `/chats/{chat_id}/pin/{msg_id}`
Закрепить сообщение. Идемпотентно — повторный вызов с тем же `msg_id` ничего не меняет. Лимит — **10 закреплённых сообщений на чат**, при превышении `400`. Ответ: обновлённый `list[MessageOut]`.

---

### DELETE `/chats/{chat_id}/pin/{msg_id}`
Открепить конкретное сообщение. Ответ: обновлённый `list[MessageOut]`.

---

### DELETE `/chats/{chat_id}/pin`
Открепить все сообщения чата. Ответ: `[]`.

---

### GET `/chats/settings`
Глобальные настройки вида чатов текущего пользователя (цвета пузырей, текста, шрифт). Если не настраивались — все поля `null`.

### PATCH `/chats/settings`
Обновить глобальные настройки. Все поля опциональны — устанавливаются только переданные. Ответ: `ChatSettingsOut`.

### GET `/chats/{chat_id}/settings`
Настройки для конкретного чата. Если per-chat override не создан — возвращает глобальные. Ответ: `ChatSettingsOut`.

### PATCH `/chats/{chat_id}/settings`
Создать или обновить per-chat override настроек. Приоритет над глобальными. Ответ: `ChatSettingsOut`.

### DELETE `/chats/{chat_id}/settings`
Удалить per-chat override (откат к глобальным настройкам). `204 No Content`.

### GET `/chats/{chat_id}/attachments`
Все вложения чата: изображения, голосовые, документы, видео. Параметр `type` — фильтр по типу:
- `image` — изображения
- `voice` — голосовые сообщения
- `document` — документы
- `video` — видео

```json
[
  {
    "id": 5,
    "message_id": 42,
    "attachment_type": "image",
    "file_url": "/static/photos/abc.jpg",
    "file_name": "photo.jpg",
    "file_size_bytes": 204800,
    "mime_type": "image/jpeg",
    "duration_seconds": null,
    "created_at": "2026-06-10T14:30:00Z"
  }
]
```
Без `type` — возвращаются все вложения. Отсортированы от новых к старым.

---

`ChatSettingsOut`:
```json
{
  "user_id": 8,
  "chat_id": 7,
  "own_bubble_color": "#DCF8C6",
  "other_bubble_color": "#FFFFFF",
  "bot_bubble_color": "#E8E8E8",
  "own_text_color": "#000000",
  "other_text_color": "#000000",
  "bot_text_color": "#555555",
  "nick_color": "#128C7E",
  "font_size": 14,
  "wallpaper_url": "/static/photos/bg.jpg",
  "wallpaper_id": null
}
```
`chat_id: null` — глобальные настройки.

Обои — три состояния:
- **default** — `wallpaper_url: null`, `wallpaper_id: null`
- **пресет** (клиентский градиент по ключу) — `wallpaper_url: null`, `wallpaper_id: "forest"`
- **своё изображение** — `wallpaper_url: "/static/photos/bg.jpg"`, `wallpaper_id: "custom"`

Оба поля передаются в `PATCH .../settings` вместе с цветами и `font_size` — отдельного эндпоинта для обоев в составе настроек нет (есть только legacy `PATCH /chats/{chat_id}/wallpaper`, не зависит от `ChatSettings`).

---

## Рут `operator` — операторский интерфейс

### GET `/operator/chats/settings`
### PATCH `/operator/chats/settings`
### GET `/operator/chats/{chat_id}/settings`
### PATCH `/operator/chats/{chat_id}/settings`
### DELETE `/operator/chats/{chat_id}/settings`
Персональные настройки вида чата (цвета, шрифт, обои) для авторизованного оператора/админа — зеркало `/chats/settings` и `/chats/{chat_id}/settings`. Владение чатом не требуется (настройки привязаны к самому оператору, не к чату). Семантика идентична пользовательским: global + per-chat override, `DELETE` сбрасывает override к глобальным. Ответы — `ChatSettingsOut` / `204`.

---

### GET `/operator/chats/{chat_id}/attachments`
Все вложения чата. Параметр `type`: `image` / `voice` / `document` / `video`. Ответ — такой же список `ChatAttachmentOut` как в `/chats/{chat_id}/attachments`.

---

### GET `/operator/chats`
Все `cabinet` и `support` чаты. Параметры:
- `search` — поиск по имени/телефону пользователя, номеру/типу/названию ШУ

Сортировка: сначала ожидающие оператора (`operator_requested=true`), затем по последнему сообщению.

Каждый чат содержит `user_id`, `user_name`, `cabinet_object_number`.

> **Оптимизация:** запрос выполняется за 3 DB-запроса независимо от числа чатов (JOIN на User+Cabinet + batch unread counts + batch last messages), вместо 4N+1 в предыдущей версии.

---

### GET `/operator/chats/{chat_id}/pinned`
Список закреплённых сообщений чата (до 10). Сортировка: от недавно закреплённых к старым. Ответ: `list[MessageOut]`, пустой массив — ничего не закреплено.

---

### GET `/operator/chats/{chat_id}/messages`
История сообщений чата. Параметры:
- `before_id` — cursor pagination (старее указанного ID)
- `around_id` — сообщения вокруг указанного ID (для перехода к результату поиска)
- `after_id` — сообщения новее указанного ID (догрузка вниз после `around_id`)
- `limit` — количество (по умолч. `30`, максимум `100`)
- `search` — поиск по тексту сообщений

`before_id`, `around_id`, `after_id` взаимоисключающие — при одновременной передаче приоритет: `around_id` → `after_id` → `before_id`.

Личные чаты пользователя (`chat_type=notes`) недоступны оператору/админу — `403`.

---

### POST `/operator/chats/{chat_id}/messages`
Отправить сообщение от имени оператора.

---

### POST `/operator/chats/{chat_id}/take`
Взять чат — бот замолкает (`bot_active=false`), `operator_requested=false`. `204 No Content`.

---

### POST `/operator/chats/{chat_id}/return-to-bot`
Вернуть чат боту (`bot_active=true`, `bot_no_count=0`). `204 No Content`.

---

### DELETE `/operator/chats/{chat_id}`
Удалить чат (оператор/админ). `204 No Content`.

---

### DELETE `/operator/chats/{chat_id}/messages`
Очистить историю чата — soft-delete всех сообщений (тексты обнуляются, `deleted_at` проставляется). `204 No Content`.

---

### PUT `/operator/chats/{chat_id}/pin/{msg_id}`
Закрепить сообщение. Идемпотентно, лимит 10 на чат (`400` при превышении). Ответ: обновлённый `list[MessageOut]`.

---

### DELETE `/operator/chats/{chat_id}/pin/{msg_id}`
Открепить конкретное сообщение. Ответ: обновлённый `list[MessageOut]`.

---

### DELETE `/operator/chats/{chat_id}/pin`
Открепить все сообщения чата. Ответ: `[]`.

---

### GET `/operator/messages?q=...`
Поиск сообщений по тексту **во всех** `cabinet` и `support` чатах. Параметры:
- `q` — строка поиска (обязателен, 1–200 символов)
- `page`, `size`

Ответ (`PageOut[MessageSearchOut]`):
```json
{
  "items": [
    {
      "id": 42,
      "chat_id": 7,
      "chat_type": "cabinet",
      "cabinet_object_number": "29_099",
      "chat_user_id": 8,
      "sender_id": 8,
      "sender_name": "Иванов Иван",
      "text": "...найденный текст...",
      "created_at": "2026-06-10T14:30:00Z",
      "attachments": []
    }
  ],
  "total": 3, "page": 1, "size": 20, "pages": 1
}
```

---

### GET `/operator/chats/unread-count`
Быстрый бейдж — количество чатов (`cabinet` + `support`), в которых есть хотя бы одно непрочитанное сообщение от пользователя.

Ответ:
```json
{ "count": 3 }
```

---

## Realtime (SSE) для операторской панели

Замена поллинга (`GET /operator/chats` каждые 4с, `GET /operator/chats/{id}/messages` каждые 3с) на push через Server-Sent Events. Все мутации (отправка/редактирование/удаление сообщения, реакции, pin) остаются обычными REST-вызовами как раньше — SSE только уведомляет, что что-то изменилось.

**Почему SSE, а не WebSocket:** операторская панель ничего не шлёт по каналу, только слушает — SSE проще и не требует HTTP-апгрейда на уровне nginx.

### Авторизация: одноразовый тикет

`EventSource` не умеет слать заголовок `Authorization`, а класть в URL сам JWT означало бы, что access-токен светится в логах nginx. Поэтому сначала обычным REST-запросом с Bearer-токеном получаем короткоживущий одноразовый тикет, а он уже подставляется в query-параметр SSE-запроса.

### POST `/operator/events/ticket`
**Доступ:** `operator`, `admin`.

Ответ:
```json
{ "ticket": "kQ2f...", "expires_in": 30 }
```
Тикет одноразовый и живёт 30 секунд — подключаться нужно сразу после получения. Просрочен/уже использован → следующий SSE-запрос ответит `401`.

---

### GET `/operator/events/chats?ticket=...`
Глобальный канал — замена поллинга списка чатов (`chats-page.tsx`). Событие `chat.created`/`chat.updated` приходит при новом сообщении (в т.ч. от бота), новом чате, смене `bot_active`/`operator_requested`/`problem_status`.

### GET `/operator/events/chats/{chat_id}?ticket=...`
Канал открытого чата — замена поллинга сообщений (`chat-conversation.tsx`). События: `message.created`, `message.updated`, `message.deleted`, `message.reaction_changed`, `message.pinned`, `message.unpinned`.

Формат конверта (`text/event-stream`):
```
event: message.created
data: {"type":"message.created","chat_id":11,"data":{...MessageOut...}}

```
Сразу после подключения приходит `{"type":"connected"}`, а затем каждые 20с — комментарий-пинг (`: ping`) для поддержания соединения, если реальных событий нет.

**Важно про payload `chat.updated`/`chat.created`:** `data` — best-effort снимок (`id`, `chat_type`, `cabinet_id`, `user_id`, `last_message_text`, `problem_status`, `bot_active`, `operator_requested`), **без** `unread_count` — он зависит от конкретного оператора-получателя и здесь не считается. Событие стоит трактовать как сигнал инвалидировать React Query кэш и перезапросить актуальные данные, а не как источник истины для всех полей.

**Ограничения, о которых стоит знать:**
- Доставка *at-most-once* и без буферизации на сервере: если соединение оборвалось (сон вкладки, сеть), пропущенные события не повторяются. При (пере)подключении стоит один раз перезапросить актуальные данные через обычный REST, а не полагаться только на поток.
- `DELETE /operator/chats/{chat_id}` (удаление чата целиком) событие не публикует — крайне редкое действие, обычно инициируется тем же оператором, который смотрит на список.
- Работает в рамках одного процесса api (см. `Dockerfile` — uvicorn без `--workers`). Если когда-нибудь потребуется несколько инстансов api, канал нужно будет перевести на Redis pub/sub.

Пример на фронте:
```js
const { ticket } = await fetch('/operator/events/ticket', {
  method: 'POST', headers: { Authorization: `Bearer ${token}` },
}).then(r => r.json());

const es = new EventSource(`/operator/events/chats?ticket=${ticket}`);
es.onmessage = (e) => {
  const event = JSON.parse(e.data);
  if (event.type === 'chat.updated' || event.type === 'chat.created') {
    queryClient.invalidateQueries(['operator-chats']);
  }
};
// ticket живёт 30с — если EventSource переподключается (реконнект браузера
// после разрыва), нужен свежий тикет через тот же POST /operator/events/ticket
```

---

## Рут `admin/dashboard` — дашборд

### GET `/admin/dashboard`
Единый endpoint для главного экрана администратора/оператора. Возвращает все счётчики одним запросом (5 DB-запросов) и последние 10 действий по всем типам заявок.

**Доступ:** `operator`, `admin`.

Ответ (`DashboardOut`):
```json
{
  "stats": {
    "unread_chats": 2,
    "open_service_requests": 5,
    "pending_document_requests": 3,
    "pending_share_requests": 1,
    "pending_addition_requests": 4
  },
  "recent_activity": [
    {
      "id": 12,
      "type": "service",
      "status": "open",
      "user_id": 8,
      "user_full_name": "Иванов Иван",
      "cabinet_id": 3,
      "created_at": "2026-06-24T10:00:00Z"
    }
  ]
}
```

Поле `type`: `service` | `document` | `share` | `addition`.

---

## Рут `service requests` — сервисные заявки

Типы заявок (`request_type`): `repair`, `maintenance`, `inspection`, `other`.
Статусы: `open`, `in_progress`, `closed`. Переход между статусами не валидируется — администратор/оператор может установить любой статус через `PATCH /admin/service-requests/{req_id}/status` (типичный сценарий: `open → in_progress → closed`).

### POST `/service-requests`
Создать заявку. Пользователь должен быть привязан к указанному ШУ.
```json
{
  "cabinet_id": 5,
  "request_type": "repair",
  "description": "Не работает кнопка управления, при нажатии нет реакции"
}
```
`description` — минимум 10 символов.

После создания заявка асинхронно (в фоне, не блокируя ответ) синхронизируется с Bitrix24 — создаётся задача (`tasks.task.add`) на фиксированного ответственного (`BITRIX_DEFAULT_RESPONSIBLE_ID`). ID созданной задачи попадает в поле `bitrix_task_id` (появится не сразу, а чуть позже создания заявки — обнови список, чтобы увидеть). Если Bitrix не настроен или недоступен — заявка всё равно создаётся, просто `bitrix_task_id` остаётся `null`.

---

### GET `/service-requests`
Свои заявки. Параметры: `status=open|in_progress|closed`, `page`, `size`.

---

### GET `/admin/service-requests`
Все заявки (для админа/оператора). Параметры:
- `status`, `cabinet_id`
- `request_type` — `repair` / `maintenance` / `inspection` / `other` (точное совпадение)
- `search` — поиск по ФИО/телефону/организации пользователя, номеру/названию ШУ, типу и описанию заявки
- `sort_by` — `created_at` (по умолч.), `closed_at`, `status`, `user_full_name`, `cabinet_object_number`, `request_type`
- `sort_order` — `asc` / `desc`
- `page`, `size`

```json
{
  "items": [
    {
      "id": 1,
      "user_id": 8,
      "user_full_name": "Иванов Иван",
      "user_phone": "+375291234567",
      "user_type": "individual",
      "organization_name": null,
      "user_is_verified": true,
      "user_registered_at": "2025-11-01T08:30:00Z",
      "cabinet_id": 5,
      "cabinet_object_number": "29_099",
      "request_type": "repair",
      "description": "Не работает кнопка",
      "status": "open",
      "created_at": "2026-05-15T10:00:00Z",
      "closed_at": null
    }
  ],
  "total": 5, "page": 1, "size": 20, "pages": 1
}
```

---

### PATCH `/admin/service-requests/{req_id}/status`
Изменить статус заявки.
```json
{ "status": "in_progress" }
```
Если у заявки есть `bitrix_task_id`, статус асинхронно (в фоне) прокидывается и в задачу Bitrix24: `open` → "Ждёт выполнения", `in_progress` → "Выполняется", `closed` → "Завершена". Направление одностороннее — изменение статуса задачи прямо в Bitrix24 к нам не возвращается.

---

## Рут `notifications` — уведомления

### GET `/notifications`
Список уведомлений текущего пользователя. Параметры: `is_read=true|false`, `page`, `size`.

```json
{
  "items": [
    {
      "id": 1,
      "type": "request_status",
      "title": "Заявка одобрена",
      "body": "Ваш ШУ был успешно добавлен",
      "data": { "cabinet_id": 5 },
      "is_read": false,
      "created_at": "2026-05-15T10:00:00Z"
    }
  ],
  "total": 3, "page": 1, "size": 20, "pages": 1
}
```

Типы уведомлений (`type`), фактически отправляемые на данный момент:
- `warranty_expiring` — гарантия истекает через 30/10/1 день
- `promotional` — рекламное сообщение от администратора
- `chat_message` — новое сообщение от оператора или бота в чате
- `operator_requested` — пользователь запросил оператора (отправляется всем активным операторам и администраторам)

Зарезервированы в настройках уведомлений, но пока не генерируются кодом:
- `request_status` — изменился статус заявки (ШУ, документ, сервисная)

---

### POST `/notifications/{notif_id}/read`
Отметить одно уведомление как прочитанное. `204 No Content`.

---

### POST `/notifications/read-all`
Отметить все уведомления пользователя как прочитанные. `204 No Content`.

---

### DELETE `/notifications`
Удалить все уведомления текущего пользователя. `204 No Content`.

---

### GET `/notifications/settings`
Настройки уведомлений пользователя.
```json
{
  "chat_messages": true,
  "promotional": false,
  "warranty_expiring": true,
  "request_status_change": true
}
```

---

### PATCH `/notifications/settings`
Изменить настройки. Передавать только те поля которые нужно поменять.
```json
{ "promotional": true }
```

---

### POST `/device-tokens`
Зарегистрировать FCM-токен устройства для получения push-уведомлений. Вызывается после логина или при обновлении токена.
```json
{
  "token": "fcm-device-token-here",
  "platform": "android"
}
```
`platform`: `android` или `ios`. `204 No Content`.

---

### DELETE `/device-tokens/{token}`
Удалить FCM-токен устройства. Вызывается при логауте чтобы устройство перестало получать push. `204 No Content`.

---

### POST `/admin/notifications/broadcast`
Разослать promotional уведомление. Только для администратора.
```json
{
  "title": "Новое обновление",
  "body": "Доступна новая версия документации",
  "role": null
}
```
- `role` — если `null`, рассылка всем активным пользователям. Если указать `"user"`, `"operator"` или `"admin"` — только по этой роли.

---

## Рут `admin: kb` — база знаний (администратор/оператор)

База знаний — файловый репозиторий, организованный по категориям. Каждая запись может содержать несколько файлов любых типов (PDF, Word, Excel, видео, фото). При создании запись сразу публикуется (`is_published=true`); снять публикацию (сделать черновиком) можно через `PATCH .../articles/{id}` с `is_published: false`.

> Просмотр категорий (`GET /admin/kb/categories`) доступен администратору и оператору. Создание/редактирование/удаление категорий и записей, а также добавление/удаление вложений — только для администратора.

### POST `/admin/kb/categories`
Создать категорию. Категории могут быть вложенными через `parent_id`.
```json
{
  "name": "Руководства",
  "parent_id": null,
  "description": "Технические руководства по оборудованию",
  "sort_order": 0
}
```

---

### GET `/admin/kb/categories`
Список категорий. Параметры (все опциональны, ответ — плоский список, без пагинации):
- `search` — поиск по названию и описанию
- `parent_id` — фильтр по родительской категории
- `sort_by` — `sort_order` (по умолч.), `name`
- `sort_order` — `asc` (по умолч.) / `desc`

---

### PATCH `/admin/kb/categories/{cat_id}`
Редактировать категорию (все поля опциональны).

---

### DELETE `/admin/kb/categories/{cat_id}`
Удалить категорию. Каскадно удаляются все записи и их файлы. Только для администратора.

---

### GET `/admin/kb/articles`
Список записей базы знаний (админ/оператор, включая неопубликованные). Параметры:
- `category_id`, `tag_ids` (`?tag_ids=1&tag_ids=2`)
- `is_published` — `true` / `false` — без параметра возвращаются записи в любом статусе
- `search` — поиск по заголовку и содержимому
- `sort_by` — `created_at` (по умолч.), `updated_at`, `title`, `version`, `is_published`
- `sort_order` — `asc` / `desc`
- `page`, `size`

Публичный аналог — `GET /kb/articles` (см. ниже) — всегда показывает только `is_published=true`.

---

### POST `/admin/kb/articles`
Создать запись в базе знаний (сразу публикуется, `is_published=true`).
```json
{
  "category_id": 1,
  "title": "Паспорт ШУ вентиляционной установки",
  "description": "Техническая документация для ВУ-100"
}
```
Ответ — полная запись с пустым списком вложений. После создания добавляй файлы через `/attachments`.

---

### PATCH `/admin/kb/articles/{article_id}`
Редактировать заголовок, описание, категорию или статус публикации записи (все поля опциональны, включая `is_published`).

---

### DELETE `/admin/kb/articles/{article_id}`
Удалить запись со всеми вложениями. Только для администратора.

---

### POST `/admin/kb/articles/{article_id}/attachments`
Добавить файл к записи. `multipart/form-data`, поле `file`.

Поддерживаемые форматы: PDF, Word, Excel, изображения (jpg/png/webp), видео (mp4/mov).

Ответ:
```json
{
  "id": 1,
  "article_id": 3,
  "file_url": "/static/documents/abc123.pdf",
  "file_size_bytes": 204800,
  "doc_type": "pdf",
  "mime_type": "application/pdf",
  "title": "manual.pdf",
  "created_at": "2026-05-15T10:00:00Z"
}
```

---

### DELETE `/admin/kb/articles/{article_id}/attachments/{att_id}`
Удалить вложение из записи. Только для администратора.

---

### PUT `/admin/kb-articles/{article_id}/tags`
Привязать теги к записи KB (полная замена). Пустой список — снять все теги.
```json
{ "tag_ids": [1, 2] }
```

---

## Рут `kb` — база знаний для пользователя

### GET `/kb/categories`
Все категории базы знаний.
```json
[
  {
    "id": 1,
    "parent_id": null,
    "name": "Руководства",
    "slug": "rukovodstva-a1b2c3",
    "description": "Технические руководства",
    "sort_order": 0
  }
]
```

---

### GET `/kb/articles`
Список записей. Параметры:
- `category_id` — фильтр по категории
- `tag_ids` — фильтр по тегам (`?tag_ids=1&tag_ids=2`)
- `search` — поиск по заголовку **и содержимому**
- `sort_by` — `created_at` (по умолч.), `updated_at`, `title`
- `sort_order` — `asc` / `desc`
- `page`, `size` — пагинация

```json
{
  "items": [
    {
      "id": 3,
      "category_id": 1,
      "title": "Паспорт ШУ вентиляционной установки",
      "slug": "pasport-shu-a1b2c3",
      "description": "Техническая документация для ВУ-100",
      "created_at": "2026-05-15T10:00:00Z",
      "tags": [{ "id": 1, "name": "паспорт", "scope": "document" }],
      "attachment_count": 3
    }
  ],
  "total": 12, "page": 1, "size": 20, "pages": 1
}
```

---

### GET `/kb/articles/{article_id}`
Полная запись с описанием и всеми вложениями.
```json
{
  "id": 3,
  "category_id": 1,
  "title": "Паспорт ШУ вентиляционной установки",
  "slug": "pasport-shu-a1b2c3",
  "description": "Техническая документация",
  "version": 1,
  "created_at": "2026-05-15T10:00:00Z",
  "updated_at": "2026-05-15T10:00:00Z",
  "tags": [{ "id": 1, "name": "паспорт" }],
  "attachments": [
    {
      "id": 1,
      "article_id": 3,
      "file_url": "/static/documents/abc.pdf",
      "file_size_bytes": 204800,
      "doc_type": "pdf",
      "mime_type": "application/pdf",
      "title": "passport.pdf",
      "created_at": "2026-05-15T10:00:00Z"
    }
  ]
}
```

---

### GET `/kb/articles/{article_id}/attachments/{att_id}/download`
Скачать / открыть файл. Возвращает бинарный файл с правильным `Content-Type`.

Добавить в избранное — через `POST /favorites { "entity_type": "kb_article", "entity_id": 3 }`.

---

## Рут `admin: faq` — часто задаваемые вопросы (администратор/оператор)

### POST `/admin/faq/categories`
Создать категорию FAQ.
```json
{
  "name": "Гарантия",
  "parent_id": null,
  "sort_order": 0
}
```

---

### GET `/admin/faq/categories`
Список категорий. Параметры (все опциональны, ответ — плоский список, без пагинации):
- `search` — поиск по названию
- `parent_id` — фильтр по родительской категории
- `sort_by` — `sort_order` (по умолч.), `name`
- `sort_order` — `asc` (по умолч.) / `desc`

---

### PATCH `/admin/faq/categories/{cat_id}`
Обновить категорию (все поля опциональны).

---

### DELETE `/admin/faq/categories/{cat_id}`
Удалить категорию. Только для администратора.

---

### POST `/admin/faq/entries`
Создать вопрос и ответ сразу. Создаётся неопубликованным (`is_published=false`) — опубликовать через `PATCH` (см. ниже).
```json
{
  "category_id": 1,
  "question": "Как продлить гарантию?",
  "answer": "Для продления гарантии обратитесь к оператору через чат поддержки."
}
```
`question` — минимум 5 символов. `answer` — минимум 1 символ.

---

### GET `/admin/faq/entries`
Список вопросов. Параметры:
- `category_id`
- `is_published` — `true` / `false` — без параметра возвращаются вопросы в любом статусе
- `search` — поиск по вопросу и ответу
- `sort_by` — `created_at` (по умолч.), `updated_at`, `question`, `version`, `is_published`
- `sort_order` — `asc` / `desc`
- `page`, `size`

---

### PATCH `/admin/faq/entries/{entry_id}`
Обновить вопрос, ответ и/или статус публикации (передавать только изменённые поля).
```json
{ "answer": "Обновлённый ответ", "is_published": true }
```

---

### DELETE `/admin/faq/entries/{entry_id}`
Удалить вопрос. Только для администратора.

---

## Рут `faq` — FAQ для пользователя

### GET `/faq/categories`
Все категории FAQ.
```json
[
  { "id": 1, "parent_id": null, "name": "Гарантия", "sort_order": 0 }
]
```

---

### GET `/faq/entries`
Список вопросов. Параметры:
- `category_id` — фильтр по категории
- `search` — поиск по тексту **вопроса и ответа**
- `sort_by` — `created_at` (по умолч.), `updated_at`, `question`
- `sort_order` — `asc` / `desc`
- `page`, `size` — пагинация

> В отличие от `/kb/articles`, этот эндпоинт **не фильтрует** по `is_published` — показывает вопросы в любом статусе публикации. Оставлено намеренно: включение фильтра потребует сначала опубликовать через админку существующие вопросы (иначе они разом пропадут для пользователей).

```json
{
  "items": [
    {
      "id": 1,
      "category_id": 1,
      "question": "Как продлить гарантию?",
      "answer": "Для продления гарантии обратитесь к оператору через чат поддержки.",
      "version": 1,
      "created_at": "2026-05-18T10:00:00Z",
      "updated_at": "2026-05-18T10:00:00Z"
    }
  ],
  "total": 5, "page": 1, "size": 20, "pages": 1
}
```

---

## RAG-бот Ася

Виртуальный ассистент, встроенный в чаты. Отвечает на вопросы пользователей по ШУ, используя базу знаний, FAQ и документацию ШУ.

### Принцип работы

```
Пользователь пишет сообщение
        ↓
Текст запроса → Yandex Embeddings (text-search-query) → вектор запроса
        ↓
pgvector cosine search → до 5 ближайших чанков из embeddings
  (если cabinet-чат → до 4 чанков ищутся среди документов конкретного ШУ,
   остальные — по всей базе embeddings; для больших документов (десятки
   чанков) лимит стоит поднимать дальше, если бот всё ещё не находит ответ,
   который точно есть в тексте)
        ↓
История диалога (до 5 предыдущих сообщений) + текущий вопрос с найденными чанками → YandexGPT-lite
        ↓
Ответ бота + цитата из источника → сохраняется в messages (sender = "Ася")
```

Ответ генерируется в фоне (`asyncio.create_task`) — пользователь не ждёт, основной запрос возвращается мгновенно.

### Источники данных

| Тип | Метка в ответе | Индексируется |
|---|---|---|
| Записи FAQ | `[FAQ]` | При создании/изменении |
| Статьи базы знаний | `[База знаний]` | При создании/изменении/добавлении вложений |
| Документы ШУ | `[Документация ШУ]` | При загрузке |

### Поддерживаемые форматы вложений/документов

Перед индексацией любой файл нормализуется в обычный текст — дальше он чанкуется и эмбеддится одинаково, независимо от исходного формата.

| Формат | Извлечение текста |
|---|---|
| PDF с текстовым слоем | pypdf, постранично |
| PDF-скан (без текстового слоя) | автоматический fallback: встроенные изображения страницы (через pypdf, без poppler) распознаются через **Yandex Vision OCR** |
| `.docx` | python-docx — параграфы и таблицы |
| `.xlsx` | openpyxl — все листы, ячейки построчно |
| Изображения (`.jpg`, `.jpeg`, `.png`, `.webp`) | Yandex Vision OCR напрямую |
| `.doc` (Word 97-2003) | **не поддерживается** — пересохранить как `.docx` |
| `.xls` (Excel 97-2003) | **не поддерживается** — пересохранить как `.xlsx` |

OCR — тот же аккаунт/ключ Yandex Cloud, что и для YandexGPT/SpeechKit (`YANDEX_API_KEY`, `YANDEX_FOLDER_ID`), новых переменных окружения не требует. Для скана распознаётся только то, что реально не текстовое — если в PDF часть страниц с текстовым слоем, а часть — сканы, OCR прогоняется только по вторым (экономит вызовы API).

Если после индексации текст пустой — смотрите логи (`docker compose logs api | grep -i "разобрать\|извлеч.*пуст\|OCR"`): там указана точная причина (не найден файл, не удалось открыть, пустой текстовый слой, не поддерживаемый формат).

### Формат ответа бота

Бот даёт краткий ответ и добавляет цитату из найденного источника:
```
Гарантия на оборудование SAVT составляет 24 месяца с даты ввода в эксплуатацию.

> "Гарантийный срок устанавливается равным 24 месяцам со дня подписания акта ввода в эксплуатацию."
```

### Счётчик попыток и передача оператору

- Если пользователь 3 раза подряд выражает недовольство (`bot_max_attempts`) — бот предлагает оператора.
- Пользователь отвечает «да» → `operator_requested=true`, `bot_active=false`. Все активные операторы и администраторы получают push-уведомление `operator_requested` с номером чата.
- Пользователь отвечает «нет» → счётчик сбрасывается, бот продолжает.
- Оператор забирает чат: `POST /operator/chats/{chat_id}/take` → бот замолкает.
- Оператор возвращает чат боту: `POST /operator/chats/{chat_id}/return-to-bot`.

### Определение решённой проблемы

Если пользователь пишет «спасибо», «помогло», «разобрался» и т.п. — бот фиксирует `problem_status=resolved`, отправляет прощальное сообщение и прекращает follow-up цикл.

### Follow-up

Если пользователь написал и не получил решения (не отвечал дольше `BOT_FOLLOW_UP_MINUTES` минут) — бот сам пишет: «Удалось ли решить вашу проблему?». Follow-up отправляется только один раз — сбрасывается лишь когда пользователь снова выражает недовольство.

### Настройка (.env)

```env
YANDEX_FOLDER_ID=ваш_folder_id          # Folder ID из Yandex Cloud
YANDEX_API_KEY=ваш_api_key              # API-ключ сервисного аккаунта
YANDEX_GPT_MODEL=yandexgpt-lite         # Модель (yandexgpt-lite / yandexgpt)
BOT_FOLLOW_UP_MINUTES=60                # Через сколько минут бот пишет follow-up
BOT_MAX_ATTEMPTS=3                      # Сколько попыток до предложения оператора
```

> **Важно:** `YANDEX_FOLDER_ID` должен совпадать с folder сервисного аккаунта, к которому выдан `YANDEX_API_KEY`. Если это разные папки — Yandex вернёт 400.

### Первый запуск

После деплоя запустить полную индексацию (один раз):
```
POST /admin/bot/reindex?force=true
Authorization: Bearer {admin_token}
```
Ответ приходит сразу, не дожидаясь окончания (считает в фоне — иначе на большом объёме упрётесь в `504` от nginx):
```json
{ "status": "started", "message": "Индексация запущена в фоне, результат смотрите в логах" }
```
Итог смотреть в логах: `docker compose logs api --tail=50 | grep -i переиндекс`.

В дальнейшем `POST /admin/bot/reindex` (без `force`) индексирует только новые записи — уже проиндексированные пропускаются (`skipped`). Также индексация происходит автоматически при создании/изменении FAQ, KB и документов.

Если что-то было удалено в обход обычных эндпоинтов (см. `POST /admin/bot/prune` в разделе «Рут `admin: bot`» ниже) — эта ручка подчищает embeddings, оставшиеся от удалённых записей.

### Управление через БД (если нужно сбросить состояние)

```sql
-- Включить бота для всех чатов
UPDATE chats SET bot_active = true WHERE chat_type != 'notes';

-- Сбросить счётчик неудачных попыток
UPDATE chats SET bot_no_count = 0, follow_up_sent = false;

-- Посмотреть сколько чанков проиндексировано
SELECT source_type, COUNT(*) FROM embeddings GROUP BY source_type;
```

---

## Рут `admin: audit` — журнал действий

### GET `/admin/audit-logs`
Журнал административных действий (создание/удаление пользователей, баны, верификации и т.п.). Доступно администратору и оператору.

Параметры:
- `actor_id` — фильтр по ID исполнителя
- `actor_role` — фильтр по роли: `admin` / `operator` / `user` / `system`
- `action` — фильтр по типу действия (строка)
- `entity_type` — фильтр по типу сущности (строка)
- `entity_id` — фильтр по ID сущности
- `date_from`, `date_to` — диапазон дат (ISO 8601)
- `search` — поиск по тексту
- `search_in` — где искать: `all` (по умолч.) / `action` / `entity_type` / `actor_name` / `payload`
- `sort_by` — `created_at` (по умолч.) / `action` / `entity_type` / `actor_role` / `actor_id`
- `sort_order` — `asc` / `desc`
- `page`, `size` — пагинация (по умолч. `1` / `50`, максимум `200`)

---

## Рут `admin: bot` — управление ботом

### POST `/admin/bot/reindex`
Индексация источников (FAQ, база знаний, документы ШУ). Только для администратора.

Параметры:
- `force=false` (по умолчанию) — индексирует только записи без существующих эмбеддингов (быстро)
- `force=true` — полная переиндексация всего (медленно, пропорционально объёму данных)

Считает в фоне — сам HTTP-запрос отвечает сразу (`202 Accepted`), не дожидаясь окончания. При заметном объёме данных `force=true` — это десятки/сотни синхронных вызовов Yandex API (эмбеддинги), что легко превышает `proxy_read_timeout` nginx, если считать синхронно внутри запроса.

Ответ:
```json
{ "status": "started", "message": "Индексация запущена в фоне, результат смотрите в логах" }
```
Итоговую статистику (`{"faq": 2, "kb_article": 0, "document": 5, "skipped": 28}`) смотреть в логах api (`docker compose logs api --tail=50 | grep -i переиндекс`) — там же видна ошибка, если фон упал.

На практике при штатной работе `skipped` почти всегда будет равен количеству всех существующих записей, а добавленных — `0`: создание и изменение записей уже индексируется автоматически в фоне (см. ниже), эта ручка нужна в основном для данных, попавших в БД в обход обычных create/update-эндпоинтов (восстановление из бэкапа, ручные правки), либо если фоновая автоиндексация когда-то не отработала.

### POST `/admin/bot/prune`
Удаляет embeddings, чей источник (FAQ/статья КБ/документ) больше не существует. Только для администратора.

Основной сценарий, когда это нужно: удаление категории каскадно сносит её статьи/вопросы на уровне БД (`ondelete=CASCADE`) — это происходит в обход сервисного `delete()`, который обычно чистит embeddings сам. С 2026-07-14 сам каскад категорий уже чистит embeddings дочерних записей заранее, так что `prune` — это подстраховка на нештатные случаи (восстановление из бэкапа, ручные правки в БД), а не единственная защита.

Ответ:
```json
{ "status": "ok", "removed": { "faq": 0, "kb_article": 3, "document": 0 } }
```

---

## Фоновые задачи (APScheduler)

Планировщик запускается автоматически при старте приложения.

### Уведомления об истечении гарантии

**Расписание:** каждый день в 09:00.

**Логика:**
- Находит все ШУ, у которых `warranty_ends_at` наступает ровно через 30, 10 или 1 день.
- Для каждого ШУ отправляет уведомление всем привязанным пользователям.
- Уважает настройки уведомлений пользователя — если `warranty_expiring: false`, push не приходит.
- Каждое уведомление (cabinet_id + порог в днях) отправляется **ровно один раз** — повторная отправка защищена таблицей `warranty_notif_log`.

**Пример push-уведомления:**
```
Заголовок: Гарантия истекает
Текст:     Гарантия ШУ «ШУ-18К» истекает через 30 дней
Data:      { "cabinet_id": 5, "days_left": 30 }
```

Тип уведомления в БД: `warranty_expiring`.

---

## Бэкапы базы данных

Скрипт `backup.sh` делает дамп PostgreSQL из Docker-контейнера, сжимает и хранит последние 5 файлов.

Гарантии скрипта:
- Дамп пишется во временный файл `.part` и переименовывается только при успехе — упавший `pg_dump` не оставит битый бэкап, который вытеснил бы хорошие при ротации.
- Пустой дамп считается ошибкой.
- Ротация: хранятся `BACKUP_KEEP_COUNT` (по умолчанию 5) последних файлов.
- Опционально копия выгружается в Yandex Object Storage (`BACKUP_S3_UPLOAD=1`) — переживёт смерть диска сервера. Использует ключи `YANDEX_STORAGE_*` из контейнера api, кладёт в `db-backups/` бакета.

Переменные окружения скрипта: `POSTGRES_DB` (умолч. `savt`), `POSTGRES_USER` (умолч. `postgres`), `BACKUP_KEEP_COUNT` (умолч. `5`), `BACKUP_S3_UPLOAD` (умолч. `0`), `BACKUP_DB_CONTAINER` / `BACKUP_API_CONTAINER` (умолч. `savt-backend-db-1` / `savt-backend-api-1`). Пароль БД не нужен — `pg_dump` внутри контейнера ходит через локальный сокет.

### Первый запуск (на сервере)

```bash
# Сделать скрипт исполняемым
chmod +x savt-backend/backup.sh

# Проверить вручную
cd savt-backend
./backup.sh
```

Бэкапы сохраняются в `savt-backend/backups/` с именем `savt_backup_YYYY-MM-DD_HH-MM-SS.sql.gz`.

### Автоматический запуск через cron

```bash
crontab -e
```

Добавить строку (запуск каждый день в 03:00, с выгрузкой в Object Storage):

```
0 3 * * * cd /path/to/savt-backend && BACKUP_S3_UPLOAD=1 ./backup.sh >> /var/log/savt-backup.log 2>&1
```

> Замените `/path/to/savt-backend` на реальный путь к директории на сервере.
> Если бакет Object Storage не настроен — уберите `BACKUP_S3_UPLOAD=1`.

### Проверить, что бэкапы реально делаются

```bash
crontab -l | grep backup            # строка на месте?
tail -n 20 /var/log/savt-backup.log # последние запуски без ошибок?
ls -lh savt-backend/backups/        # 5 файлов со свежими датами?
```

### Восстановление из бэкапа

```bash
# Распаковать и восстановить
gunzip -c backups/savt_backup_2026-05-18_03-00-00.sql.gz \
    | docker exec -i savt-backend-db-1 psql -U postgres -d savt
```

---

### Follow-up бота

**Расписание:** каждые 10 минут.

**Логика:**
- Находит все чаты где `bot_active=true`, `follow_up_sent=false`, и последнее сообщение пользователя старше `BOT_FOLLOW_UP_MINUTES` минут (по умолчанию 60).
- Отправляет от имени Аси: «Здравствуйте! Удалось ли решить вашу проблему? Если нет — я готов помочь.»
- Ставит `follow_up_sent=true` — повторно не пишет до нового сообщения от пользователя.

---

## Управление через CLI

```bash
# Создать суперадмина (только через CLI — намеренно нет API)
docker exec savt-backend-api-1 python -m app.cli create-superadmin <login> <password> [full_name]

# Создать администратора через CLI (альтернатива API POST /admin/users/admins, доступна суперадмину)
docker exec savt-backend-api-1 python -m app.cli create-admin <login> <password> [full_name]

# Создать оператора через CLI (альтернатива API POST /admin/users/operators)
docker exec savt-backend-api-1 python -m app.cli create-operator <login> <password> [full_name]
```

| Кто создаёт | Через CLI | Через API |
|---|---|---|
| Суперадмин | ✅ | ❌ только CLI |
| Администратор | ✅ | ✅ `POST /admin/users/admins` (суперадмин) |
| Оператор | ✅ | ✅ `POST /admin/users/operators` (администратор) |
