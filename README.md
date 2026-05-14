# savtAssistApp-server

## Тестовые пользователи

| Роль  | Логин              | Пароль           |
|-------|--------------------|------------------|
| admin | admin              | 123qweASDZXC     |
| user  | +375291002030      | qweasdzxc        |

---

## Работа с Docker

### Сборка и запуск контейнеров
```bash
# docker compose up -d --build   - билд

# docker compose exec api alembic upgrade head    - миграции
# docker compose exec api python -m app.cli create-admin admin "123qweASDZXC" "Admin Admen"   - создание админа

# docker compose logs api --tail=10 - логи

# БД действия
# docker ps - посмотреть все контейнеры
# docker exec -it savt-backer-db-1 psql -U postges - все свои данные
# \l - выводит все бдшки, ищем свою savt
# \c savt - подключаемся к сааавт
# \dt - все таблички бдшки
# \d <название таблички> - посмотреть структуру таблички
# ну и кнтр z чтобы ливнуть с этого
```

# Добро пожаловать в savtAssistApp-server - сервер для мобильного приложения поддержки-SAVT.

С помощью этой API вы сможете создавать манипулировать пользователями, шкафами, документацией, qr-кодами, 
чатами, сервисными заявками, уведомлениями, базой знаний и FAQ-разделом.

Есть 3 роли пользователя:
1. Пользователь - может добавлять шкаф, пользоваться чатом-поддержки, просматривать/запрашивать документацию, создавать сервисные заявки.
2. Оператор - отвечает пользователю в чат-поддержке.
3. Администратор - может управлять всеми данными, бог системы.

Далее будут предметно описаны эндпоинты приложения:

## Рут auth:

Используется для авторизации/ренгистрации пользователя в систему:

- ### Post запрос /auth/register/start 
  Начало регистрации пользователя, вводит все свои данные:
```bash
  {
  "phone": "string",
  "password": "string",
  "password_confirm": "string",
  "full_name": "string",
  "user_type": "string",
  "organization_name": "string"
}
```
  phone - номер телефона, с проверкой на корректность
  password - пароль, минимум 8 символов
  password-confirm - подтверждение пароля, должен совпадать с паролем
  full_name - полное имя пользователя
  user_type - тип пользователя, частное или организационное лицо, проверка на тип, частное - individual, организационное - organization
  organization_name - необходимо если тип пользователя - organization
  После вернет:
```bash
  {
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 0
}
```
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода
  
- ### Post запрос /auth/register/complete
  После успешного заполнения всех данных на телефон приходит код подтверждения действующий 60 секунд
```bash
  {
  "phone": "string",
  "code": "string"
}
```
  phone - номер телефона, его нужно передать ещё раз, для сверки
  code - код пришедший на этот номер телефона
  После вернет:
```bash
  {
  "access_token": "string",
  "refresh_token": "string",
  "token_type": "bearer"
}
```
  access_token - аксес токен
  refresh_token - рефреш токен
  token_type - тип токена

- ### Post запрос /auth/register/resend
  Если же вдруг, пользователь не успел ввести за 60 секунд код, или же не пришел код, то его можно получить ещё раз
```bash
  {
  "phone": "string"
}
```
  phone - номер телефона на который придет код повторно
  После вернет:
```bash
  {
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 0
}
```
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода

- ### Post запрос /auth/login
  Авторизация пользователя в систему
```bash
  {
  "phone": "string",
  "password": "string"
}
```
  phone - номер телефона для логина пользователя
  password - пароль пользователя
  После вернет:
```bash
  {
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 0
}
```
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода

- ### Post запрос /auth/admin/login
  Авторизация администратора в систему
```bash
  {
  "login": "string",
  "password": "string"
}
```
  login - логин админа
  password - пароль админа
  После вернет:
```bash
  {
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 0
}
```
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода

- ### Post запрос /auth/refresh
  Обновление аксес токена
```bash
  {
  "refresh_token": "string"
}
```
  refresh_token - текущий рефреш токен
  После вернет:
```bash
  {
  "access_token": "string",
  "refresh_token": "string",
  "token_type": "bearer"
}
```
  access_token - аксес токен для продления сессии
  refresh_token - рефреш токен
  token_type - тип токена

- ### Post запрос /auth/logout
  Выход аккаунта из всех устройств
```bash
  {
  "refresh_token": "string"
}
```
  refresh_token - текущий рефреш токен
  После удалит токен

- ### Get запрос /auth/me
  Если пользователь авторизован - вернет ему данные об аккаунте

- ### Post запрос /auth/password-reset/start
  Восстановление пароля, при клике пользователь вводит свой номер телефона и на этот номер приходит код
```bash
  {
  "phone": "string"
}
```
  phone - номер телефона
  После вернет:
```bash
  {
  "message": "На телефон отправлен код",
  "resend_after_seconds": 0
}
```
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода

- ### Post запрос /auth/password-reset/complete
  После того как код пришел - пользователь его вводит и заполняет данные о новом пароле
```bash
  {
  "phone": "string",
  "code": "string",
  "new_password": "stringst",
  "new_password_confirm": "stringst"
}
```
  phone - номер телефона
  code - код подтверждения
  new_password - новый пароль, минимум 8 символов
  new_password_confirm - подтверждение нового пароля, должен совпадать с новым паролем
  Если всё успешно - пароль поменяется

- ### Post запрос /auth/password-change
  Смена пароля
```bash
  {
  "password": "stringst",
  "new_password": "stringst",
  "new_password_confirm": "stringst"
}
```
  password - старый пароль
  new_password - новый пароль
  new_password_confirm - подтверждение нового пароля
  После успешного заполнения пароль поменяется


## Рут admin: cabinets:

Используется для манипуляции шкафами от лица админа:

- ### Post запрос /admin/cabinets
  Администратор заполняем все ниже перечисленные данные:
```bash
  {
  "type": "string",
  "object_number": "string",
  "description": "string",
  "warranty_starts_at": "2026-05-14T07:07:06.300Z",
  "warranty_ends_at": "2026-05-14T07:07:06.300Z",
  "admin_internal_name": "string",
  "admin_comment": "string",
  "purpose": "string"
}
```
  type - тип ШУ
  object_number - номер объекта
  description - описание ШУ
  warranty_starts_at - время начала гарантии
  warranty_ends_at - время окончания гарантии
  admin_internal_name - название ШУ(отображается у администратора и у пользователя до смены на кастомное название пользователя)
  admin_comment - комментарий админа по поводу ШУ(только у админа)
  purpose - назначение ШУ
  После вернет:
```bash
  {
  "id": 0,
  "unique_code": "string",
  "type": "string",
  "object_number": "string",
  "description": "string",
  "warranty_starts_at": "2026-05-14T07:07:06.301Z",
  "warranty_ends_at": "2026-05-14T07:07:06.301Z",
  "admin_internal_name": "string",
  "admin_comment": "string",
  "purpose": "string",
  "created_at": "2026-05-14T07:07:06.301Z",
  "updated_at": "2026-05-14T07:07:06.301Z"
}
```
  id - айдииии
  unique_code - уникальный код ШУ
  type - тип ШУ
  object_number - номер объекта
  description - описание ШУ
  warranty_starts_at - время начала гарантии
  warranty_ends_at - время окончания гарантии
  admin_internal_name - название ШУ
  admin_comment - комментарий админа по поводу ШУ
  purpose - назначение ШУ
  created_at - время создания ШУ
  updated_at - время обновления ШУ

- ### Get запрос /admin/cabinets
  Можно задавать такие параметры как:
  search(string(какое-то слово) или null)
  sort_by(type(тип), warranty_ends_at(дата окончания), object_number(номер объекта), created_at(дата создания))
  sort_order(asc(по возрастанию), desc(по убыванию))
  После вернет:
```bash
[
  {
    "id": 0,
    "unique_code": "string",
    "object_number": "string",
    "warranty_starts_at": "2026-05-14T07:18:06.435Z",
    "warranty_ends_at": "2026-05-14T07:18:06.435Z",
    "admin_internal_name": "string",
    "created_at": "2026-05-14T07:18:06.435Z"
  }
]
```
  id - айдиии
  unique_code - уникальный код ШУ
  object_number - номер объекта
  warranty_starts_at - время начала гарантии
  warranty_ends_at - время окончания гарантии
  admin_internal_name - название ШУ
  created_at - время создания ШУ

- ### Get запрос /admin/cabinets/{cabinet_id}
  Возвращает подробную информацию о ШУ, принимает параметр cabinet_id
  После вернет:
```bash
  {
  "id": 0,
  "unique_code": "string",
  "type": "string",
  "object_number": "string",
  "description": "string",
  "warranty_starts_at": "2026-05-14T07:38:00.130Z",
  "warranty_ends_at": "2026-05-14T07:38:00.130Z",
  "admin_internal_name": "string",
  "admin_comment": "string",
  "purpose": "string",
  "created_at": "2026-05-14T07:38:00.130Z",
  "updated_at": "2026-05-14T07:38:00.130Z"
}
```
  id - айдиии
  unique_code - уникальный код ШУ
  type - тип ШУ
  object_number - номер объекта
  description - описание ШУ
  warranty_starts_at - время начала гарантии
  warranty_ends_at - время окончания гарантии
  admin_internal_name - название ШУ
  admin_comment - комментарий админа
  purpose - назначение ШУ
  created_at - время создания ШУ
  updated_at - время обновления ШУ

- ### Patch запрос /admin/cabinets/{cabinet_id}
  Обновление данных о ШУ, принимает параметр cabinet_id
  Данные доступные для обновление
```bash
  {
  "type": "string",
  "object_number": "string",
  "description": "string",
  "warranty_starts_at": "2026-05-14T07:44:59.964Z",
  "warranty_ends_at": "2026-05-14T07:44:59.964Z",
  "admin_internal_name": "string",
  "admin_comment": "string",
  "purpose": "string"
}
```
  type - тип ШУ
  object_number - номер объекта
  descriprion - описание ШУ
  warranty_starts_at - время начала гарантии
  warranty_ends_at - время окончания гарантии
  admin_internal_name - название ШУ
  admin_comment - комментарий админа
  purpose - назначение ШУ
  После вернет:
```bash
  {
  "id": 0,
  "unique_code": "string",
  "type": "string",
  "object_number": "string",
  "description": "string",
  "warranty_starts_at": "2026-05-14T07:44:59.970Z",
  "warranty_ends_at": "2026-05-14T07:44:59.970Z",
  "admin_internal_name": "string",
  "admin_comment": "string",
  "purpose": "string",
  "created_at": "2026-05-14T07:44:59.970Z",
  "updated_at": "2026-05-14T07:44:59.970Z"
}
```
  id - айдии
  unique_code - уникальный код
  type - тип ШУ
  object_number - номер объекта
  description - описание ШУ
  warranty_starts_at - время начала гарантии
  warranty_ends_at - время окончания гарантии
  admin_internal_name - название ШУ
  admin_comment - комментарий ШУ
  purpose - назначение ШУ
  created_at - дата создания ШУ
  updated_at - дата обновление ШУ

- ### Delete запрос /admin/cabinets/{cabinet_id}
  Удаления ШУ, принимает параметр cabinet_id
  После удалит ШУ


## Рут admin: cabinets-requests:

Используется для управления запросами по ШУ:

- ### Get запрос /admin/cabinet-requests/additions 
  Получение всех запросов на добавление ШУ по фото, можно по параметру status(pending(ожидание),approved(апрувнутые),rejected(посланы)):
  После вернет:
```bash
[
  {
    "id": 0,
    "user_id": 0,
    "user_full_name": "string",
    "user_phone": "string",
    "photo_url": "string",
    "user_comment": "string",
    "status": "string",
    "cabinet_id": 0,
    "admin_response": "string",
    "created_at": "2026-05-14T07:54:23.954Z",
    "resolved_at": "2026-05-14T07:54:23.954Z"
  }
]
```
  id - айди
  user_id - id(айди) пользователя(от кого поступил запрос)
  user_full_name - ФИО дурачка
  user_phone - номер телефона чела
  photo_utl - ссылка на фото ШУ
  user_comment - комментарий пользователя
  status - статус заявки
  cabinet_id - айди ШУ
  admin_response - ответ админа по заявке
  created_at - дата создания заявки
  resolved_at - дата обработки заявки

- ### Post запрос /admin/cabinet-requests/additions/{request_id}/approve
  Апрув заявки, принимает request_id и необходимо ввести данные:
```bash
  {
  "cabinet_id": 0,
  "admin_response": "string"
}
```
  cabinet_id - айди ШУ(чел же не знает что за ШУ, есть только фото)
  admin_response - ответ админа
  После вернет заявка примет статус апрув

- ### Post запрос /admin/cabinet-requests/additions/{request_id}/reject
  Отказ в заявке, принимает request_id и необходимо ввести данные:
```bash
  {
  "admin_response": "string"
}
```
  admin_response - ответ админа
  После вернет заявка примет статус отказа

- ### Get запрос /admin/cabinet-requests/shares
  Получение всех запросов на добавление ШУ пользователю(если уже у кого-то есть этот ШУ), можно по параметру status(pending(ожидание),approved(апрувнутые),rejected(посланы)):
  После вернет:
```bash
[
  {
    "id": 0,
    "user_id": 0,
    "user_full_name": "string",
    "user_phone": "string",
    "cabinet_id": 0,
    "cabinet_type": "string",
    "cabinet_object_number": "string",
    "user_comment": "string",
    "status": "string",
    "admin_response": "string",
    "created_at": "2026-05-14T08:07:53.707Z",
    "resolved_at": "2026-05-14T08:07:53.707Z"
  }
]
```
  id - айди
  user_id - id(айди) пользователя(от кого поступил запрос)
  user_full_name - ФИО дурачка
  user_phone - номер телефона чела
  cabinet_id - айди ШУ
  cabinet_type - тип ШУ
  cabinet_object_number - номер объекта ШУ
  user_comment - комментарий пользователя
  status - статус заявки
  admin_response - ответ админа по заявке
  created_at - дата создания заявки
  resolved_at - дата обработки заявки

- ### Post запрос /admin/cabinet-requests/shares/{request_id}/approve
  Апрув заявки, принимает request_id и необходимо ввести данные:
```bash
  {
  "admin_response": "string"
}
```
  admin_response - ответ админа
  После вернет заявка примет статус апрув

- ### Post запрос /admin/cabinet-requests/shares/{request_id}/reject
  Отказ в заявке, принимает request_id и необходимо ввести данные:
```bash
  {
  "admin_response": "string"
}
```
  admin_response - ответ админа
  После вернет заявка примет статус отказа
