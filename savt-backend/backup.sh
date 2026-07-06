#!/bin/bash
# Бэкап PostgreSQL из Docker-контейнера.
# Хранит последние KEEP_COUNT дампов, старые удаляет автоматически.
# Дамп пишется во временный файл и переименовывается только при успехе —
# упавший pg_dump не оставит битый файл, который вытеснил бы хорошие бэкапы.
# Опционально выгружает копию в Yandex Object Storage (BACKUP_S3_UPLOAD=1).

set -euo pipefail

# ── настройки (можно переопределить через переменные окружения) ──────────────
CONTAINER="${BACKUP_DB_CONTAINER:-savt-backend-db-1}"
API_CONTAINER="${BACKUP_API_CONTAINER:-savt-backend-api-1}"
DB_NAME="${POSTGRES_DB:-savt}"
DB_USER="${POSTGRES_USER:-postgres}"
BACKUP_DIR="$(dirname "$0")/backups"
KEEP_COUNT="${BACKUP_KEEP_COUNT:-5}"
# BACKUP_S3_UPLOAD=1 — дополнительно выгружать дамп в Yandex Object Storage
# (использует boto3 и ключи YANDEX_STORAGE_* из контейнера api)
S3_UPLOAD="${BACKUP_S3_UPLOAD:-0}"
# ─────────────────────────────────────────────────────────────────────────────

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
FILENAME="savt_backup_${TIMESTAMP}.sql.gz"
FILEPATH="${BACKUP_DIR}/${FILENAME}"
TMPPATH="${FILEPATH}.part"

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запуск бэкапа → ${FILENAME}"

# pg_dump внутри контейнера ходит через локальный сокет — пароль не нужен
if ! docker exec "$CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$TMPPATH"; then
    rm -f "$TMPPATH"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ОШИБКА: pg_dump завершился с ошибкой, бэкап не сохранён" >&2
    exit 1
fi

# Пустой дамп — тоже ошибка (например, контейнер поднялся, но БД не та)
if [ ! -s "$TMPPATH" ]; then
    rm -f "$TMPPATH"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ОШИБКА: дамп пустой, бэкап не сохранён" >&2
    exit 1
fi

mv "$TMPPATH" "$FILEPATH"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Готово. Размер: $(du -sh "$FILEPATH" | cut -f1)"

# Копия в Yandex Object Storage — переживёт смерть диска сервера
if [ "$S3_UPLOAD" = "1" ]; then
    if docker exec -i "$API_CONTAINER" python -c "
import os, sys, boto3
bucket = os.environ.get('YANDEX_STORAGE_BUCKET', '')
if not bucket:
    sys.exit('YANDEX_STORAGE_BUCKET не задан в .env')
s3 = boto3.client(
    's3',
    endpoint_url=os.environ.get('YANDEX_STORAGE_ENDPOINT_URL', 'https://storage.yandexcloud.net'),
    aws_access_key_id=os.environ['YANDEX_STORAGE_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['YANDEX_STORAGE_SECRET_ACCESS_KEY'],
)
s3.upload_fileobj(sys.stdin.buffer, bucket, 'db-backups/${FILENAME}')
" < "$FILEPATH"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Копия выгружена в Object Storage: db-backups/${FILENAME}"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ВНИМАНИЕ: выгрузка в Object Storage не удалась (локальный бэкап сохранён)" >&2
    fi
fi

# Удаляем старые бэкапы, оставляем только KEEP_COUNT последних
EXISTING=$(ls -1t "${BACKUP_DIR}"/savt_backup_*.sql.gz 2>/dev/null | wc -l)
if [ "$EXISTING" -gt "$KEEP_COUNT" ]; then
    ls -1t "${BACKUP_DIR}"/savt_backup_*.sql.gz \
        | tail -n +"$((KEEP_COUNT + 1))" \
        | xargs rm -f
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Удалено старых бэкапов: $((EXISTING - KEEP_COUNT))"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Бэкапов сохранено: $(ls -1 "${BACKUP_DIR}"/savt_backup_*.sql.gz 2>/dev/null | wc -l)/${KEEP_COUNT}"
