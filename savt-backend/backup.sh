set -euo pipefail

# В дампе вся база (телефоны, имена, хеши паролей): файлы и папка читаются
# только владельцем
umask 077

CONTAINER="${BACKUP_DB_CONTAINER:-savt-backend-db-1}"
API_CONTAINER="${BACKUP_API_CONTAINER:-savt-backend-api-1}"
DB_NAME="${POSTGRES_DB:-savt}"
DB_USER="${POSTGRES_USER:-postgres}"
BACKUP_DIR="$(dirname "$0")/backups"
KEEP_COUNT="${BACKUP_KEEP_COUNT:-5}"
S3_UPLOAD="${BACKUP_S3_UPLOAD:-0}"
NAS_COPY="${BACKUP_NAS_COPY:-0}"
# Путь внутри api-контейнера: шара NAS с проектами смонтирована в /mnt/projects
NAS_DIR="${BACKUP_NAS_DIR:-/mnt/projects/_db-backups}"
NAS_KEEP_COUNT="${BACKUP_NAS_KEEP_COUNT:-30}"
# Файл с паролем шифрования копии на NAS (пусто — копия кладётся без шифрования)
NAS_PASSPHRASE_FILE="${BACKUP_NAS_PASSPHRASE_FILE:-}"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
FILENAME="savt_backup_${TIMESTAMP}.sql.gz"
FILEPATH="${BACKUP_DIR}/${FILENAME}"
TMPPATH="${FILEPATH}.part"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запуск бэкапа → ${FILENAME}"

if ! docker exec "$CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$TMPPATH"; then
    rm -f "$TMPPATH"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ОШИБКА: pg_dump завершился с ошибкой, бэкап не сохранён" >&2
    exit 1
fi

if [ ! -s "$TMPPATH" ]; then
    rm -f "$TMPPATH"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ОШИБКА: дамп пустой, бэкап не сохранён" >&2
    exit 1
fi

mv "$TMPPATH" "$FILEPATH"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Готово. Размер: $(du -sh "$FILEPATH" | cut -f1)"

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

# Копия на шару NAS через api-контейнер (шара уже смонтирована там для папок
# проектов). К шаре есть доступ у сотрудников, а в дампе вся база, поэтому
# при заданном NAS_PASSPHRASE_FILE копия шифруется. Расшифровка:
#   openssl enc -d -aes-256-cbc -pbkdf2 -pass file:ФАЙЛ_ПАРОЛЯ -in savt_backup_….sql.gz.enc | gunzip
if [ "$NAS_COPY" = "1" ]; then
    NAS_NAME="$FILENAME"
    [ -n "$NAS_PASSPHRASE_FILE" ] && NAS_NAME="${FILENAME}.enc"

    send_to_nas() {
        if [ -n "$NAS_PASSPHRASE_FILE" ]; then
            openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:${NAS_PASSPHRASE_FILE}" < "$FILEPATH"
        else
            cat "$FILEPATH"
        fi
    }

    if send_to_nas | docker exec -i "$API_CONTAINER" sh -c \
        "mkdir -p '${NAS_DIR}' && cat > '${NAS_DIR}/${NAS_NAME}.part' && mv '${NAS_DIR}/${NAS_NAME}.part' '${NAS_DIR}/${NAS_NAME}'"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Копия выложена на NAS: ${NAS_DIR}/${NAS_NAME}"
        docker exec "$API_CONTAINER" sh -c \
            "ls -1t '${NAS_DIR}'/savt_backup_* 2>/dev/null | tail -n +$((NAS_KEEP_COUNT + 1)) | xargs -r rm -f" || true
    else
        docker exec "$API_CONTAINER" rm -f "${NAS_DIR}/${NAS_NAME}.part" || true
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ВНИМАНИЕ: копия на NAS не удалась (локальный бэкап сохранён)" >&2
    fi
fi

EXISTING=$(ls -1t "${BACKUP_DIR}"/savt_backup_*.sql.gz 2>/dev/null | wc -l)
if [ "$EXISTING" -gt "$KEEP_COUNT" ]; then
    ls -1t "${BACKUP_DIR}"/savt_backup_*.sql.gz \
        | tail -n +"$((KEEP_COUNT + 1))" \
        | xargs rm -f
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Удалено старых бэкапов: $((EXISTING - KEEP_COUNT))"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Бэкапов сохранено: $(ls -1 "${BACKUP_DIR}"/savt_backup_*.sql.gz 2>/dev/null | wc -l)/${KEEP_COUNT}"
