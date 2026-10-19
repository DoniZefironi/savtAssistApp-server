#!/bin/bash
# Проверка, что ночные бэкапы свежие и на диске есть место. При проблеме шлёт
# сообщение в Telegram и завершается с кодом 1. Запускается из cron уже после
# backup.sh, поэтому ловит любой сбой, в том числе когда сам backup.sh не
# запустился (слетел флаг «исполняемый», упал cron, выключен сервер).
#
#   bash backup-check.sh          — обычная проверка
#   bash backup-check.sh --test   — отправить тестовое сообщение, проверить связь
set -uo pipefail

cd "$(dirname "$0")"

# Куда слать: ваш chat_id в Telegram (узнаётся у @userinfobot), а у самого бота
# (TELEGRAM_BOT_USERNAME из .env) должна быть нажата кнопка «Старт» — иначе
# Telegram не даст боту писать первым
CHAT_ID="${BACKUP_ALERT_CHAT_ID:-}"
MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
MIN_FREE_PERCENT="${BACKUP_MIN_FREE_PERCENT:-15}"
API_CONTAINER="${BACKUP_API_CONTAINER:-savt-backend-api-1}"
NAS_COPY="${BACKUP_NAS_COPY:-0}"
NAS_DIR="${BACKUP_NAS_DIR:-/mnt/projects/_db-backups}"
BACKUP_DIR="$(pwd)/backups"

problems=()

if [ "${1:-}" = "--test" ]; then
    problems+=("тестовое сообщение: связь с Telegram работает")
fi

newest=$(ls -1t "$BACKUP_DIR"/savt_backup_*.sql.gz 2>/dev/null | head -1)
if [ -z "$newest" ]; then
    problems+=("в ${BACKUP_DIR} нет ни одного бэкапа")
else
    age_hours=$(( ($(date +%s) - $(stat -c %Y "$newest")) / 3600 ))
    if [ "$age_hours" -ge "$MAX_AGE_HOURS" ]; then
        problems+=("последнему бэкапу ${age_hours} ч ($(basename "$newest"))")
    fi
fi

# Возраст самой свежей копии на NAS считается внутри api-контейнера (шара смонтирована там)
if [ "$NAS_COPY" = "1" ]; then
    nas_age=$(docker exec "$API_CONTAINER" sh -c \
        'f=$(ls -1t "$1"/savt_backup_* 2>/dev/null | head -1); [ -n "$f" ] && echo $(( ($(date +%s) - $(stat -c %Y "$f")) / 3600 ))' \
        sh "$NAS_DIR" 2>/dev/null)
    if [ -z "$nas_age" ]; then
        problems+=("копия на NAS не найдена, либо NAS или api-контейнер недоступны")
    elif [ "$nas_age" -ge "$MAX_AGE_HOURS" ]; then
        problems+=("копии на NAS ${nas_age} ч")
    fi
fi

# Заполненный диск ломает и бэкапы, и загрузку файлов, и саму базу
used_percent=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ -n "$used_percent" ] && [ $((100 - used_percent)) -lt "$MIN_FREE_PERCENT" ]; then
    problems+=("на диске сервера свободно меньше ${MIN_FREE_PERCENT}% (занято ${used_percent}%)")
fi

if [ "${#problems[@]}" -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Проверка бэкапов: всё в порядке"
    exit 0
fi

message="SAVT Assist, сервер $(hostname): проблемы с бэкапами"$'\n'
for problem in "${problems[@]}"; do
    message+="- ${problem}"$'\n'
done
echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${message}" >&2

TOKEN=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2- | tr -d '"\r ')
if [ -z "$TOKEN" ] || [ -z "$CHAT_ID" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ОШИБКА: оповещение не отправлено — нет TELEGRAM_BOT_TOKEN в .env или BACKUP_ALERT_CHAT_ID" >&2
    exit 1
fi

# Адрес с токеном передаётся через stdin, а не аргументом: иначе токен виден
# другим пользователям сервера в списке процессов
if printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TOKEN" \
    | curl -fsS -m 20 -K - --data-urlencode "chat_id=${CHAT_ID}" --data-urlencode "text=${message}" > /dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Оповещение отправлено в Telegram"
    [ "${1:-}" = "--test" ] && exit 0
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ОШИБКА: не удалось отправить оповещение в Telegram" >&2
fi
exit 1
