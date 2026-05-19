#!/bin/bash
# Бэкап PostgreSQL из Docker-контейнера.
# Хранит последние KEEP_COUNT дампов, старые удаляет автоматически.

set -euo pipefail

# ── настройки ────────────────────────────────────────────────────────────────
CONTAINER="savt-backend-db-1"
DB_NAME="${POSTGRES_DB:-savt}"
DB_USER="${POSTGRES_USER:-postgres}"
BACKUP_DIR="$(dirname "$0")/backups"
KEEP_COUNT=5
# ─────────────────────────────────────────────────────────────────────────────

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
FILENAME="savt_backup_${TIMESTAMP}.sql.gz"
FILEPATH="${BACKUP_DIR}/${FILENAME}"

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запуск бэкапа → ${FILENAME}"

docker exec "$CONTAINER" \
    pg_dump -U "$DB_USER" "$DB_NAME" \
    | gzip > "$FILEPATH"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Готово. Размер: $(du -sh "$FILEPATH" | cut -f1)"

# Удаляем старые бэкапы, оставляем только KEEP_COUNT последних
EXISTING=$(ls -1t "${BACKUP_DIR}"/savt_backup_*.sql.gz 2>/dev/null | wc -l)
if [ "$EXISTING" -gt "$KEEP_COUNT" ]; then
    ls -1t "${BACKUP_DIR}"/savt_backup_*.sql.gz \
        | tail -n +"$((KEEP_COUNT + 1))" \
        | xargs rm -f
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Удалено старых бэкапов: $((EXISTING - KEEP_COUNT))"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Бэкапов сохранено: $(ls -1 "${BACKUP_DIR}"/savt_backup_*.sql.gz 2>/dev/null | wc -l)/${KEEP_COUNT}"
