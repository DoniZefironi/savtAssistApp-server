# savtAssistApp-server

## Тестовые пользователи

| Роль     | Логин              | Пароль           |
|----------|--------------------|------------------|
| admin    | admin              | 123qweASDZXC     |
| operator | operator           | 123qweASDZXC     |
| user     | +375291002030      | qweasdzxc        |

---

## Переменные окружения (.env)

| Переменная | Описание |
|---|---|
| `DATABASE_URL` | Строка подключения к PostgreSQL |
| `JWT_SECRET_KEY` | Секрет для подписи JWT |
| `JWT_ACCESS_TOKEN_TTL_MINUTES` | Время жизни access-токена (мин) |
| `JWT_REFRESH_TOKEN_TTL_DAYS` | Время жизни refresh-токена (дни) |
| `SMS_BY_TOKEN` | API-токен SMS-провайдера |
| `SMS_BY_ALPHANAME` | Имя отправителя SMS |
| `CORS_ORIGINS` | Разрешённые origins через запятую |
| `FIREBASE_CREDENTIALS_PATH` | Путь к JSON-ключу Firebase |
| `YANDEX_FOLDER_ID` | Folder ID сервисного аккаунта Yandex Cloud |
| `YANDEX_API_KEY` | API-ключ Yandex Cloud |
| `YANDEX_GPT_MODEL` | Модель YandexGPT (`yandexgpt-lite`) |
| `BOT_FOLLOW_UP_MINUTES` | Через сколько минут бот пишет follow-up (по умолч. 60) |
| `BOT_MAX_ATTEMPTS` | Попыток бота до предложения оператора (по умолч. 3) |

---

## Работа с Docker

```bash
# Сборка и запуск
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
Ответ:
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
Запрос SMS-кода для смены номера телефона. Код отправляется на **новый** номер.
```json
{ "new_phone": "+375291234568" }
```
Ответ: `{ "message": "...", "resend_after_seconds": 60 }`.

Ошибки:
- `409` — новый номер уже используется другим пользователем
- `409` — это уже ваш текущий номер

---

### POST `/auth/change-phone/complete`
Подтверждение смены номера кодом из SMS.
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
Загрузка вложения (фото, документ, видео).

| Тип | Форматы | Лимит |
|---|---|---|
| Изображение | jpg, png, webp | 10 МБ |
| Документ | pdf, doc, docx, xls, xlsx | 50 МБ |
| Видео | mp4, mov | 500 МБ |

Ответ:
```json
{ "url": "/static/photos/abc123.jpg" }
```

---

### POST `/upload/voice`
Загрузка голосового сообщения.

| Форматы | Лимит |
|---|---|
| ogg, mp3, m4a, wav | 25 МБ |

Ответ:
```json
{ "url": "/static/voices/abc123.ogg" }
```

---

### GET `/upload/download?url={file_url}`
Скачать любой файл (фото, документ, видео, голосовое) с заголовком `Content-Disposition: attachment`. Требует Bearer-токен.

Параметр `url` — значение поля `file_url` из вложения или `url` из фото, например `/static/photos/abc123.jpg`.

- `400` — URL не указывает на загруженный файл
- `404` — файл не найден на диске

---

## Рут `admin: cabinets` — управление ШУ (только админ)

### POST `/admin/cabinets`
Создание нового ШУ. `unique_code` генерируется автоматически (64-бит случайный код).
```json
{
  "type": "Вентиляционная установка",
  "object_number": "29_099",
  "description": "Описание",
  "warranty_starts_at": "2025-01-01T00:00:00Z",
  "warranty_ends_at": "2027-01-01T00:00:00Z",
  "admin_internal_name": "ШУ-18К",
  "admin_comment": "Комментарий для внутреннего использования",
  "purpose": "Вентиляция"
}
```
Ответ — полная информация о созданном ШУ включая `unique_code`.

---

### GET `/admin/cabinets`
Список всех ШУ. Доступно оператору и администратору. Параметры:
- `search` — поиск по типу, номеру объекта, названию, назначению, описанию, комментарию
- `tag_ids` — фильтр по тегам (`?tag_ids=1&tag_ids=2`)
- `sort_by` — `type`, `warranty_ends_at`, `object_number`, `admin_internal_name`, `created_at`
- `sort_order` — `asc`, `desc`
- `page`, `size` — пагинация (по умолч. `1` / `20`, максимум `100`)

Каждый элемент содержит:
```json
{
  "id": 1,
  "unique_code": "A3F7BC12",
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

### GET `/admin/cabinets/{cabinet_id}`
Детальная информация о ШУ с тегами.

---

### GET `/admin/cabinets/{cabinet_id}/qr`
Генерирует QR-код для ШУ в формате PNG (с логотипом SAVT если есть файл `app/assets/savt_logo.png`).

QR кодирует строку: `savt://cabinet/{unique_code}`

Ответ: бинарный PNG (`image/png`).

---

### PATCH `/admin/cabinets/{cabinet_id}`
Обновление данных ШУ (все поля опциональны). Возвращает обновлённый ШУ с тегами.

---

### PUT `/admin/cabinets/{cabinet_id}/tags`
Привязать теги к ШУ (полная замена). Только для администратора. Теги должны иметь `scope="cabinet"`.
```json
{ "tag_ids": [1, 2] }
```
Пустой список снимает все теги. `204 No Content`.

---

### DELETE `/admin/cabinets/{cabinet_id}`
Удаление ШУ. `204 No Content`.

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
- `search` — поиск по ФИО, телефону, организации пользователя
- `sort_by` — `created_at` (по умолч.), `status`, `user_full_name`
- `sort_order` — `asc` / `desc`
- `page`, `size`

```json
[
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
    "created_at": "2026-05-12T08:00:00Z",
    "resolved_at": null
  }
]
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
- `search` — поиск по ФИО/телефону пользователя, типу/номеру/названию ШУ
- `sort_by` — `created_at` (по умолч.), `status`, `user_full_name`, `cabinet_object_number`
- `sort_order` — `asc` / `desc`
- `page`, `size`

```json
[
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
    "created_at": "2026-05-12T09:00:00Z",
    "resolved_at": null
  }
]
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
Список пользователей. Параметры:
- `search` — поиск по ФИО, телефону, **логину**, **email**, организации
- `is_active` — `true` / `false`
- `role` — фильтр по роли: `user` / `operator` / `admin` / `superadmin`
- `sort_by` — `created_at` (по умолч.), `full_name`, `phone`, `email`, `role`
- `sort_order` — `asc` / `desc` (по умолч. `desc`)
- `page`, `size` — пагинация (по умолч. `page=1`, `size=20`, максимум `100`)

**Видимость по ролям:**
- Без фильтра: только `user` и `operator`
- `?role=admin`: администраторы (только суперадмин должен вызывать)
- `?role=superadmin`: суперадмины
- `bot` и удалённые операторы (`_deleted_*`) никогда не показываются

При `sort_by=role` порядок: operator → user → admin.

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
Детальная информация о пользователе включая список его ШУ с гарантийным статусом.

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
  "custom_name": "Мой шкаф",
  "custom_comment": "Комментарий",
  "is_primary": true
}
```

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
| `file` | файл (jpg/png/webp) | ✅ |
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
Заявки пользователей на доступ к закрытым документам. Параметры: `status=pending|approved|rejected`, `page`, `size`.

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
`user_message` необязателен. Ответ: `{ "request_id": 1, "message": "Заявка отправлена" }`.

---

### GET `/cabinets/{cabinet_id}/photos`
Список фото ШУ. Параметры: `page`, `size`.

---

## Рут `tags` — теги

Теги разделены по области видимости (`scope`):
- `document` — теги для документов и статей базы знаний
- `cabinet` — теги для ШУ

### GET `/tags`
Список тегов. Параметры:
- `scope` — фильтр по области: `document` / `cabinet`. Без фильтра — все теги.

```json
[
  { "id": 1, "name": "паспорт", "scope": "document" },
  { "id": 2, "name": "Электрика", "scope": "cabinet" }
]
```

---

### POST `/admin/tags`
Создать тег. Только для администратора/оператора.
```json
{ "name": "паспорт", "scope": "document" }
```
- `scope`: `document` (по умолчанию) или `cabinet`
- Имя тега уникально в рамках одной области

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

---

### GET `/chats/{chat_id}/messages`
История сообщений. Параметры:
- `before_id` — ID сообщения, загрузить более старые (cursor pagination для бесконечного скролла)
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

## Рут `operator` — операторский интерфейс

### GET `/operator/chats`
Все `cabinet` и `support` чаты. Параметры:
- `search` — поиск по имени/телефону пользователя, номеру/типу/названию ШУ

Сортировка: сначала ожидающие оператора (`operator_requested=true`), затем по последнему сообщению.

Каждый чат содержит `user_id`, `user_name`, `cabinet_object_number`.

---

### GET `/operator/chats/{chat_id}/messages`
История сообщений чата. Параметры:
- `before_id`, `limit` — cursor pagination
- `search` — поиск по тексту сообщений

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

## Рут `service requests` — сервисные заявки

Типы заявок (`request_type`): `repair`, `maintenance`, `inspection`, `other`.
Статусы: `open` → `in_progress` → `closed`.

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

---

### GET `/service-requests`
Свои заявки. Параметры: `status=open|in_progress|closed`, `page`, `size`.

---

### GET `/admin/service-requests`
Все заявки (для админа/оператора). Параметры: `status`, `cabinet_id`, `page`, `size`.

```json
{
  "items": [
    {
      "id": 1,
      "user_id": 8,
      "user_full_name": "Иванов Иван",
      "user_phone": "+375291234567",
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

Типы уведомлений (`type`):
- `chat_message` — новое сообщение от оператора/бота
- `request_status` — изменился статус заявки (ШУ, документ, сервисная)
- `warranty_expiring` — гарантия истекает через 30/10/1 день
- `promotional` — рекламное сообщение от администратора

---

### POST `/notifications/{notif_id}/read`
Отметить одно уведомление как прочитанное. `204 No Content`.

---

### POST `/notifications/read-all`
Отметить все уведомления пользователя как прочитанные. `204 No Content`.

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

База знаний — файловый репозиторий, организованный по категориям. Каждая запись может содержать несколько файлов любых типов (PDF, Word, Excel, видео, фото). Создание записи = публикация (черновиков нет).

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
Список всех категорий, отсортированный по `sort_order`.

---

### PATCH `/admin/kb/categories/{cat_id}`
Редактировать категорию (все поля опциональны).

---

### DELETE `/admin/kb/categories/{cat_id}`
Удалить категорию. Каскадно удаляются все записи и их файлы. Только для администратора.

---

### POST `/admin/kb/articles`
Создать запись в базе знаний (сразу публикуется).
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
Редактировать заголовок, описание или категорию записи.

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
Список всех категорий.

---

### PATCH `/admin/faq/categories/{cat_id}`
Обновить категорию (все поля опциональны).

---

### DELETE `/admin/faq/categories/{cat_id}`
Удалить категорию. Только для администратора.

---

### POST `/admin/faq/entries`
Создать вопрос и ответ сразу.
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
Список вопросов. Параметры: `category_id`, `search`, `page`, `size`.

---

### PATCH `/admin/faq/entries/{entry_id}`
Обновить вопрос и/или ответ (передавать только изменённые поля).
```json
{ "answer": "Обновлённый ответ" }
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
pgvector cosine search → 3 ближайших чанка из embeddings
  (если cabinet-чат → сначала ищет в документах конкретного ШУ)
        ↓
История диалога (6 последних сообщений) + найденные чанки → YandexGPT-lite
        ↓
Ответ бота + цитата из источника → сохраняется в messages (sender = "Ася")
```

Ответ генерируется в фоне (`asyncio.create_task`) — пользователь не ждёт, основной запрос возвращается мгновенно.

### Источники данных

| Тип | Метка в ответе | Индексируется |
|---|---|---|
| Записи FAQ | `[FAQ]` | При создании/изменении |
| Статьи базы знаний | `[База знаний]` | При создании/изменении/добавлении вложений |
| Документы ШУ (PDF, DOCX) | `[Документация ШУ]` | При загрузке |

### Формат ответа бота

Бот даёт краткий ответ и добавляет цитату из найденного источника:
```
Гарантия на оборудование SAVT составляет 24 месяца с даты ввода в эксплуатацию.

> "Гарантийный срок устанавливается равным 24 месяцам со дня подписания акта ввода в эксплуатацию."
```

### Счётчик попыток и передача оператору

- Если пользователь 3 раза подряд выражает недовольство (`bot_max_attempts`) — бот предлагает оператора.
- Пользователь отвечает «да» → `operator_requested=true`, `bot_active=false`.
- Пользователь отвечает «нет» → счётчик сбрасывается, бот продолжает.
- Оператор забирает чат: `POST /operator/chats/{chat_id}/take` → бот замолкает.
- Оператор возвращает чат боту: `POST /operator/chats/{chat_id}/return-to-bot`.

### Follow-up

Если пользователь написал и не получил решения (не отвечал дольше `BOT_FOLLOW_UP_MINUTES` минут) — бот сам пишет: «Удалось ли решить вашу проблему?»

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
POST /admin/bot/reindex
Authorization: Bearer {admin_token}
```
Ответ:
```json
{ "status": "ok", "indexed": { "faq": 10, "kb_article": 5, "document": 20 } }
```
Дальнейшая индексация — автоматически при создании/изменении FAQ, KB и документов.

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

## Рут `admin: bot` — управление ботом

### POST `/admin/bot/reindex`
Полная переиндексация всех источников (FAQ, база знаний, документы ШУ). Только для администратора. Занимает время пропорциональное объёму данных.

Ответ:
```json
{ "status": "ok", "indexed": { "faq": 10, "kb_article": 5, "document": 20 } }
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

### Первый запуск (на сервере)

```bash
# Сделать скрипт исполняемым
chmod +x savt-backend/backup.sh

# Проверить вручную (переменные берутся из окружения или .env)
cd savt-backend
POSTGRES_DB=savt POSTGRES_USER=postgres ./backup.sh
```

Бэкапы сохраняются в `savt-backend/backups/` с именем `savt_backup_YYYY-MM-DD_HH-MM-SS.sql.gz`.

### Автоматический запуск через cron

```bash
crontab -e
```

Добавить строку (запуск каждый день в 03:00):

```
0 3 * * * cd /path/to/savt-backend && POSTGRES_DB=savt POSTGRES_USER=postgres POSTGRES_PASSWORD=yourpassword ./backup.sh >> /var/log/savt-backup.log 2>&1
```

> Замените `/path/to/savt-backend` на реальный путь к директории на сервере.

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
- Отправляет от имени Аси: «Здравствуйте! Удалось ли решить вашу проблему?»
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
