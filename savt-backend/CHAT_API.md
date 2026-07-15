# SAVT Assist — Chat API

Справочник для фронтенда. Base URL: `https://helper.savt.by`. Авторизация — заголовок `Authorization: Bearer <access_token>`, если не указано иное.

Собрано по актуальному коду роутеров `chats.py`, `operator.py`, `operator_events.py`, `upload.py` и схем `schemas/chat.py` — без вымышленных полей.

Мутации (отправка/редактирование/удаление, реакции, пины) — обычный REST. Realtime-канал (SSE) только уведомляет о том, что что-то изменилось, см. раздел ниже.

## Общее

| | |
|---|---|
| `chat_type` | `"support"` \| `"cabinet"` \| `"notes"` |
| `problem_status` | `"open"` \| `"resolved"` |
| `attachment_type` | `"image"` \| `"video"` \| `"voice"` \| `"document"` |
| Rate limit | 200 запросов/мин **на IP** (slowapi, глобальный дефолт, не per-user) |

> **`notes`** — личный чат-заметки, виден только владельцу; операторская роль его не видит и не может открыть, в отличие от `support`/`cabinet`.

> Оператор технически может дёргать пользовательские эндпоинты без префикса `/operator` (редактирование, удаление своего сообщения, реакции — доступ проверяется одинаково для владельца чата и для operator/admin). **Обои — исключение**, там строгая проверка «только владелец чата», оператору 403 даже на чужом чате.

---

## Пользователь (Bearer обычного пользователя)

### Настройки чата

| Метод | Путь | Описание | Ответ |
|---|---|---|---|
| GET | `/chats/settings` | Глобальные настройки внешнего вида | `ChatSettingsOut` |
| PATCH | `/chats/settings` | Обновить глобальные настройки | `ChatSettingsOut` |
| GET | `/chats/{chat_id}/settings` | Настройки конкретного чата (override, иначе глобальные) | `ChatSettingsOut` |
| PATCH | `/chats/{chat_id}/settings` | Обновить override конкретного чата | `ChatSettingsOut` |
| DELETE | `/chats/{chat_id}/settings` | Сбросить override — вернуться к глобальным | `204` |

Тело PATCH — `ChatSettingsIn`, всё опционально (частичное обновление).

### Чаты и сообщения

| Метод | Путь | Описание | Ответ |
|---|---|---|---|
| GET | `/chats` | Список чатов пользователя (support, notes, cabinet) | `ChatListOut[]` |
| GET | `/cabinets/{cabinet_id}/chat` | Получить чат ШУ, создать при первом обращении | `ChatOut` (403 если ШУ не привязан) |
| GET | `/chats/{chat_id}/messages` | История сообщений | `MessageOut[]` |
| POST | `/chats/{chat_id}/messages` | Отправить сообщение | `MessageOut` (201) |
| POST | `/chats/{chat_id}/read` | Отметить все сообщения чата прочитанными | `204` |
| PATCH | `/chats/{chat_id}/messages/{msg_id}` | Редактировать своё сообщение (текст обязателен) | `MessageOut` |
| DELETE | `/chats/{chat_id}/messages/{msg_id}` | Удалить своё сообщение (soft-delete, текст стирается) | `204` |
| GET | `/chats/{chat_id}/attachments` | Все вложения чата, фильтр `?type=` | `ChatAttachmentOut[]` |

**Query-параметры `GET /chats/{chat_id}/messages`:**

| Параметр | Тип | Описание |
|---|---|---|
| `before_id` | int | курсор «старше этого id» |
| `around_id` | int | окно вокруг конкретного сообщения |
| `after_id` | int | курсор «новее этого id» — удобно для догрузки после реконнекта SSE |
| `limit` | 1–100 | по умолчанию 30 |
| `search` | 1–200 симв. | подстрока по тексту |

**Тело `POST /chats/{chat_id}/messages` — `MessageCreateIn`:**
```json
{
  "text": "Добрый день, нужна консультация",
  "reply_to_message_id": null,
  "attachments": [
    {
      "file_url": "/static/photos/ab12.jpg",
      "file_name": "фото.jpg",
      "file_size_bytes": 204800,
      "mime_type": "image/jpeg",
      "duration_seconds": null
    }
  ]
}
```
Нужен текст или хотя бы одно вложение.

### Реакции и пины

| Метод | Путь | Описание | Ответ |
|---|---|---|---|
| POST | `/chats/{chat_id}/messages/{msg_id}/reactions/{emoji}` | Поставить реакцию | `204` |
| DELETE | `/chats/{chat_id}/messages/{msg_id}/reactions/{emoji}` | Убрать свою реакцию | `204` |
| GET | `/chats/{chat_id}/pinned` | Список закреплённых сообщений | `MessageOut[]` |
| PUT | `/chats/{chat_id}/pin/{msg_id}` | Закрепить — идемпотентно, лимит 10/чат (`400` при превышении) | `MessageOut[]` |
| DELETE | `/chats/{chat_id}/pin/{msg_id}` | Открепить одно сообщение | `MessageOut[]` |
| DELETE | `/chats/{chat_id}/pin` | Открепить все сообщения чата разом | `[]` |

### Обои и удаление чата

| Метод | Путь | Описание | Ответ |
|---|---|---|---|
| PATCH | `/chats/{chat_id}/wallpaper` | Установить обои — строго владелец, оператору 403 | `ChatSettingsOut` |
| DELETE | `/chats/{chat_id}` | Удалить чат — нельзя для `support` | `204` |

Тело wallpaper: `{ "wallpaper_url": "/static/wallpapers/x.jpg" | null }`

---

## Оператор (Bearer operator/admin)

Видит все `cabinet` и `support` чаты, не только свои.

### Список и поиск

| Метод | Путь | Описание | Ответ |
|---|---|---|---|
| GET | `/operator/chats?search=` | Все чаты, `operator_requested` — наверху | `ChatListOut[]` |
| GET | `/operator/chats/unread-count` | Бейдж — сколько чатов с непрочитанным | `{ "count": 3 }` |
| GET | `/operator/messages?q=` | Полнотекстовый поиск по всем cabinet/support чатам | `PageOut<MessageSearchOut>` |

`search` — по имени/телефону пользователя или данным ШУ. `q` в поиске сообщений обязателен (1–200 симв.), есть `page`/`size` (до 100).

### Сообщения чата

| Метод | Путь | Описание | Ответ |
|---|---|---|---|
| GET | `/operator/chats/{chat_id}/messages` | История — те же курсоры, что у пользователя | `MessageOut[]` |
| POST | `/operator/chats/{chat_id}/messages` | Ответить в чате от имени оператора | `MessageOut` (201) |
| GET | `/operator/chats/{chat_id}/pinned` | Закреплённые сообщения | `MessageOut[]` |
| PUT | `/operator/chats/{chat_id}/pin/{msg_id}` | Закрепить | `MessageOut[]` |
| DELETE | `/operator/chats/{chat_id}/pin/{msg_id}` | Открепить одно | `MessageOut[]` |
| DELETE | `/operator/chats/{chat_id}/pin` | Открепить всё | `[]` |
| GET | `/operator/chats/{chat_id}/attachments` | Вложения чата, фильтр по типу | `ChatAttachmentOut[]` |

> Редактирования сообщения, точечного удаления и реакций под `/operator` нет — используйте пользовательские пути (`PATCH/DELETE /chats/{chat_id}/messages/{msg_id}`, реакции), они одинаково доступны обеим ролям.

### Управление чатом

| Метод | Путь | Описание | Ответ |
|---|---|---|---|
| POST | `/operator/chats/{chat_id}/take` | Взять чат себе — выключает бота, снимает `operator_requested` | `204` |
| POST | `/operator/chats/{chat_id}/return-to-bot` | Вернуть диалог боту Асе, сбросить счётчик попыток | `204` |
| DELETE | `/operator/chats/{chat_id}` | Удалить чат целиком | `204` |
| DELETE | `/operator/chats/{chat_id}/messages` | Очистить историю — soft-delete всех сообщений | `204` |

### Настройки внешнего вида

| Метод | Путь | Описание |
|---|---|---|
| GET | `/operator/chats/settings` | Глобальные настройки оператора |
| PATCH | `/operator/chats/settings` | Обновить глобальные |
| GET | `/operator/chats/{chat_id}/settings` | Override на конкретный чат |
| PATCH | `/operator/chats/{chat_id}/settings` | Обновить override |
| DELETE | `/operator/chats/{chat_id}/settings` | Сбросить override |

---

## Realtime — SSE-стримы оператора

Замена поллинга операторской панели. `EventSource` не умеет слать заголовок `Authorization` — сначала получаем одноразовый тикет обычным REST-запросом.

| Метод | Путь | Описание |
|---|---|---|
| POST | `/operator/events/ticket` | Выдать одноразовый тикет для подключения к потоку |
| GET | `/operator/events/chats?ticket=...` | Глобальный канал — замена поллинга списка чатов |
| GET | `/operator/events/chats/{chat_id}?ticket=...` | Канал открытого чата — замена поллинга сообщений |

Ответ тикета: `{ "ticket": "kQ2f...", "expires_in": 30 }` — одноразовый, живёт 30 секунд, подключаться нужно сразу.

**Типы событий:**

| type | Канал | Когда |
|---|---|---|
| `connected` | оба | сразу после установки соединения |
| `message.created` | `chat:{id}` | новое сообщение — от пользователя, оператора или бота |
| `message.updated` | `chat:{id}` | сообщение отредактировано |
| `message.deleted` | `chat:{id}` | сообщение удалено (только `id`) |
| `message.reaction_changed` | `chat:{id}` | реакция добавлена/снята |
| `message.pinned` | `chat:{id}` | сообщение закреплено |
| `message.unpinned` | `chat:{id}` | откреплено (в т.ч. по одному при «открепить всё») |
| `chat.created` | `operator_chats` | новый support/cabinet чат появился |
| `chat.updated` | `operator_chats` | активность в чате — сигнал перезапросить список |

Формат конверта (`text/event-stream`):
```
event: message.created
data: {"type":"message.created","chat_id":11,"data":{ ...MessageOut... }}
```

> **`chat.updated`/`chat.created`** несут best-effort снимок (`id`, `chat_type`, `cabinet_id`, `user_id`, `last_message_text`, `problem_status`, `bot_active`, `operator_requested`) — **без** `unread_count`, он зависит от конкретного оператора. Трактуйте событие как сигнал инвалидировать кэш и перезапросить список, а не как источник истины для всех полей.
>
> Доставка *at-most-once*: разрыв соединения не буферизуется на сервере. При (пере)подключении стоит один раз перезапросить актуальные данные обычным REST.

---

## Вложения — загрузка файлов

Нужны перед отправкой сообщения с вложением — сначала загружаем файл, полученный `url` кладём в `attachments[].file_url`. Доступно и пользователю, и оператору.

| Метод | Путь | Описание | Ответ |
|---|---|---|---|
| POST | `/upload/attachment` | Загрузить фото/видео/документ (multipart), до 500 МБ | `{ "url": "/static/photos/uuid.jpg" }` |
| POST | `/upload/voice` | Загрузить голосовое (multipart), до 25 МБ | `{ "url": "..." }` |
| POST | `/upload/transcribe` | Распознать голосовое в текст (Yandex SpeechKit) | `{ "text": "..." }` |
| GET | `/upload/download?url=...` | Скачать файл с заголовком `Content-Disposition: attachment` | файл |

Тело `/upload/transcribe`: `{ "file_url": "/static/voices/uuid.ogg" }`

---

## Схемы объектов

**MessageOut**
```json
{
  "id": 42, "chat_id": 11, "sender_id": 8, "sender_name": "Иванов Иван",
  "text": "Добрый день", "reply_to_message_id": null, "is_read": false,
  "created_at": "2026-07-14T10:22:00Z", "edited_at": null, "deleted_at": null,
  "attachments": [], "reactions": [{ "id": 1, "user_id": 8, "emoji": "👍" }]
}
```

**ChatListOut**
```json
{
  "id": 11, "chat_type": "cabinet", "cabinet_id": 5,
  "cabinet_name": "ШУ-18К", "cabinet_object_number": "29_099",
  "user_id": 8, "user_name": "Иванов Иван",
  "last_message_text": "Добрый день", "last_message_at": "2026-07-14T10:22:00Z",
  "unread_count": 2, "problem_status": "open",
  "bot_active": true, "operator_requested": false
}
```

**ChatOut** — `/cabinets/{cabinet_id}/chat`
```json
{
  "id": 11, "chat_type": "cabinet", "cabinet_id": 5,
  "problem_status": "open", "bot_active": true,
  "operator_requested": false, "created_at": "2026-07-14T09:00:00Z"
}
```

**ChatAttachmentOut** — `/chats/{id}/attachments`
```json
{
  "id": 3, "message_id": 42, "attachment_type": "image",
  "file_url": "/static/photos/ab12.jpg", "file_name": "фото.jpg",
  "file_size_bytes": 204800, "mime_type": "image/jpeg",
  "duration_seconds": null, "created_at": "2026-07-14T10:22:00Z"
}
```

**ChatSettingsOut** — `.../settings`
```json
{
  "user_id": 8, "chat_id": 11,
  "own_bubble_color": "#1F7A6C", "other_bubble_color": "#ECEFEC",
  "bot_bubble_color": "#F0E6D2", "own_text_color": "#FFFFFF",
  "other_text_color": "#1A1E1C", "bot_text_color": "#1A1E1C",
  "nick_color": "#B8791E", "font_size": 15,
  "wallpaper_url": null, "wallpaper_id": null
}
```
