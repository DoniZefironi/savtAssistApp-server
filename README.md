# savtAssistApp-server

## Тестовые пользователи

| Роль     | Логин              | Пароль           |
|----------|--------------------|------------------|
| admin    | admin              | 123qweASDZXC     |
| operator | operator           | 123qweASDZXC     |
| user     | +375291002030      | qweasdzxc        |

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
# Ctrl+Z — выход
```

---

# savtAssistApp — сервер мобильного приложения поддержки SAVT

API для управления пользователями, шкафами управления (ШУ), документацией, QR-кодами, чатами, сервисными заявками, уведомлениями, базой знаний и FAQ.

**Роли:**
1. **Пользователь** — добавляет ШУ, пользуется чатом поддержки, просматривает/запрашивает документацию, создаёт сервисные заявки.
2. **Оператор** — отвечает в чате, обрабатывает заявки.
3. **Администратор** — полное управление всеми данными.

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
  "is_phone_verified": true
}
```

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
Список всех ШУ. Параметры:
- `search` — поиск по типу, номеру объекта, названию
- `sort_by` — `type`, `warranty_ends_at`, `object_number`, `created_at`
- `sort_order` — `asc`, `desc`
- `page` — номер страницы (по умолчанию `1`)
- `size` — элементов на странице (по умолчанию `20`, максимум `100`)

Ответ:
```json
{
  "items": [...],
  "total": 150,
  "page": 1,
  "size": 20,
  "pages": 8
}
```

---

### GET `/admin/cabinets/{cabinet_id}`
Детальная информация о ШУ.

---

### GET `/admin/cabinets/{cabinet_id}/qr`
Генерирует QR-код для ШУ в формате PNG (с логотипом SAVT если есть файл `app/assets/savt_logo.png`).

QR кодирует строку: `savt://cabinet/{unique_code}`

Ответ: бинарный PNG (`image/png`).

---

### PATCH `/admin/cabinets/{cabinet_id}`
Обновление данных ШУ (все поля опциональны).

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

## Рут `admin: cabinet requests` — заявки по ШУ (админ/оператор)

### GET `/admin/cabinet-requests/additions`
Заявки на добавление ШУ через фото. Параметры: `status=pending|approved|rejected`, `page`, `size`

```json
[
  {
    "id": 1,
    "user_id": 8,
    "user_full_name": "Иванов Иван",
    "user_phone": "+375291234567",
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
Заявки на доступ к уже существующему ШУ (сканирован QR, но ШУ уже занят). Параметры: `status=pending|approved|rejected`, `page`, `size`

```json
[
  {
    "id": 1,
    "user_id": 10,
    "user_full_name": "Петров Пётр",
    "user_phone": "+375291111111",
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

### GET `/admin/users`
Список всех пользователей. Параметры:
- `search` — поиск по ФИО, телефону, организации
- `is_active` — `true` / `false`
- `page`, `size` — пагинация (по умолчанию `page=1`, `size=20`, максимум `100`)

```json
[
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
    "created_at": "2026-05-01T10:00:00Z"
  }
]
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

### POST `/admin/users/{user_id}/ban`
Блокировка пользователя. Только для админа. Логируется в `audit_log`.
```json
{ "reason": "Нарушение условий использования" }
```
Ответ: `204 No Content`.

---

### POST `/admin/users/{user_id}/unban`
Разблокировка пользователя. Только для админа. Логируется в `audit_log`.

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

### GET `/tags`
Список всех тегов (все авторизованные пользователи).
```json
[{ "id": 1, "name": "паспорт" }, { "id": 2, "name": "схема" }]
```

---

### POST `/admin/tags`
Создать тег.
```json
{ "name": "паспорт" }
```

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
`entity_type`: `document` или `kb_article`. Ответ: объект избранного.

---

### DELETE `/favorites/{entity_type}/{entity_id}`
Удалить из избранного. `204 No Content`.

---

### GET `/favorites`
Список избранного. Параметры: `entity_type=document|kb_article`, `page`, `size`.
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
Все `cabinet` и `support` чаты. Сортировка: сначала ожидающие оператора (`operator_requested=true`).

---

### GET `/operator/chats/{chat_id}/messages`
История сообщений чата (аналогично пользовательскому, но без ограничения владельца).

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

## Управление через CLI

```bash
# Создать администратора
docker exec savt-backend-api-1 python -m app.cli create-admin <login> <password> [full_name]

# Создать оператора
docker exec savt-backend-api-1 python -m app.cli create-operator <login> <password> [full_name]
```
