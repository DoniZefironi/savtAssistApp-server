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
  {
  "phone": "string",
  "password": "string",
  "password_confirm": "string",
  "full_name": "string",
  "user_type": "string",
  "organization_name": "string"
}
  phone - номер телефона, с проверкой на корректность
  password - пароль, минимум 8 символов
  password-confirm - подтверждение пароля, должен совпадать с паролем
  full_name - полное имя пользователя
  user_type - тип пользователя, частное или организационное лицо, проверка на тип, частное - individual, организационное - organization
  organization_name - необходимо если тип пользователя - organization
  После вернет:
  {
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 0
}
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода
  
- ### Post запрос /auth/register/complete
  После успешного заполнения всех данных на телефон приходит код подтверждения действующий 60 секунд
  {
  "phone": "string",
  "code": "string"
}
  phone - номер телефона, его нужно передать ещё раз, для сверки
  code - код пришедший на этот номер телефона
  После вернет:
  {
  "access_token": "string",
  "refresh_token": "string",
  "token_type": "bearer"
}
  access_token - аксес токен
  refresh_token - рефреш токен
  token_type - тип токена

- ### Post запрос /auth/register/resend
  Если же вдруг, пользователь не успел ввести за 60 секунд код, или же не пришел код, то его можно получить ещё раз
  {
  "phone": "string"
}
  phone - номер телефона на который придет код повторно
  После вернет:
  {
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 0
}
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода

- ### Post запрос /auth/login
  Авторизация пользователя в систему
  {
  "phone": "string",
  "password": "string"
}
  phone - номер телефона для логина пользователя
  password - пароль пользователя
  сПосле вернет:
  {
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 0
}
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода

- ### Post запрос /auth/admin/login
  Авторизация администратора в систему
  {
  "login": "string",
  "password": "string"
}
  login - логин админа
  password - пароль админа
  После вернет:
  {
  "message": "Код подтверждения отправлен",
  "resend_after_seconds": 0
}
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода

- ### Post запрос /auth/refresh
  Обновление аксес токена
  {
  "refresh_token": "string"
}
  refresh_token - текущий рефреш токен
  После вернет:
  {
  "access_token": "string",
  "refresh_token": "string",
  "token_type": "bearer"
}
  access_token - аксес токен для продления сессии
  refresh_token - рефреш токен
  token_type - тип токена

- ### Post запрос /auth/logout
  Выход аккаунта из всех устройств
  {
  "refresh_token": "string"
}
  refresh_token - текущий рефреш токен
  После удалит токен

- ### Get запрос /auth/me
  Если пользователь авторизован - вернет ему данные об аккаунте

- ### Post запрос /auth/password-reset/start
  Восстановление пароля, при клике пользователь вводит свой номер телефона и на этот номер приходит код
  {
  "phone": "string"
}
  phone - номер телефона
  После вернет:
  {
  "message": "На телефон отправлен код",
  "resend_after_seconds": 0
}
  message - уведомление о том, что код отправлен
  resend_after_seconds - время действия кода

- ### Post запрос /auth/password-reset/complete
  После того как код пришел - пользователь его вводит и заполняет данные о новом пароле
  {
  "phone": "string",
  "code": "string",
  "new_password": "stringst",
  "new_password_confirm": "stringst"
}
  phone - номер телефона
  code - код подтверждения
  new_password - новый пароль, минимум 8 символов
  new_password_confirm - подтверждение нового пароля, должен совпадать с новым паролем
  Если всё успешно - пароль поменяется

- ### Post запрос /auth/password-change
  Смена пароля
  {
  "password": "stringst",
  "new_password": "stringst",
  "new_password_confirm": "stringst"
}
  password - старый пароль
  new_password - новый пароль
  new_password_confirm - подтверждение нового пароля
  После успешного заполнения пароль поменяется
