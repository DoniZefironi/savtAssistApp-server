# savtAssistApp-server
# docker compose up -d --build
# docker compose exec api alembic upgrade head
# docker compose exec api python -m app.cli create-admin admin "123qweASDZXC" "Admin Admen"

# docker compose logs api --tail=10 - логи

# БД действия
# docker ps - посмотреть все контейнеры
# docker exec -it savt-backer-db-1 psql -U postges - все свои данные
# \l - выводит все бдшки, ищем свою savt
# \c savt - подключаемся к сааавт
# \dt - все таблички бдшки
# \d <название таблички> - посмотреть структуру таблички
# ну и кнтр z чтобы ливнуть с этого