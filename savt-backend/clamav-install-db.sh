#!/bin/bash
# Ставит свежие базы вирусов в контейнер clamav. Сервер ClamAV закрыт для адресов из некоторых
# стран (в том числе нашего сервера), поэтому контейнер сам обновиться не может: базы качает
# рабочий компьютер вне блокировки (tools/update-clamav-db.ps1) и присылает их сюда по SSH.
#
# Архив tar.gz с main, daily и bytecode (.cvd или .cld) читается со стандартного ввода:
#   ssh ключ-для-обновления сервер < clamav-db.tgz
# Ключ в ~/.ssh/authorized_keys привязан к этому скрипту (command="bash …/clamav-install-db.sh"),
# поэтому ничего другого этим ключом на сервере сделать нельзя.
#
# Порядок: размер и состав архива, подписи баз внутри контейнера (sigtool), замена файлов,
# перезагрузка демона, вывод версии баз, которую он теперь знает.
set -euo pipefail

CONTAINER="${CLAMAV_CONTAINER:-savt-backend-clamav-1}"
MAX_BYTES=300000000          # настоящий архив около 120 МБ

fail() {
    echo "ОШИБКА: $*" >&2
    exit 1
}

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

head -c "$MAX_BYTES" > "$STAGE/in.tgz"
[ -s "$STAGE/in.tgz" ] || fail "архив не получен"

# В архиве допустимы только три базы (ничего с путями и никаких других файлов)
members=$(tar tzf "$STAGE/in.tgz") || fail "это не tar.gz"
[ -n "$members" ] || fail "архив пустой"
while IFS= read -r member; do
    [[ "$member" =~ ^(\./)?(main|daily|bytecode)\.c[lv]d$ ]] || fail "лишний файл в архиве: $member"
done <<< "$members"

mkdir "$STAGE/db"
tar xzf "$STAGE/in.tgz" -C "$STAGE/db" --no-same-owner --no-same-permissions
for base in main daily bytecode; do
    ls "$STAGE/db/$base".c?d > /dev/null 2>&1 || fail "в архиве нет базы $base"
done

docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true \
    || fail "контейнер $CONTAINER не запущен"

# Подпись проверяет сама ClamAV: подделанную или битую базу sigtool не признаёт
docker exec "$CONTAINER" sh -c 'rm -rf /tmp/newdb && mkdir /tmp/newdb'
docker cp "$STAGE/db/." "$CONTAINER:/tmp/newdb/"
for file in "$STAGE"/db/*; do
    name=$(basename "$file")
    docker exec "$CONTAINER" sigtool --info "/tmp/newdb/$name" | grep -q "Verification OK" \
        || { docker exec "$CONTAINER" rm -rf /tmp/newdb; fail "подпись базы $name не подтверждена"; }
done

# Замена по одному файлу через временное имя: ClamAV читает только файлы со своими расширениями,
# поэтому недокопированный файл не подхватится
docker exec "$CONTAINER" sh -c '
    set -e
    for base in main daily bytecode; do
        new=$(ls /tmp/newdb/$base.c?d)
        cp "$new" "/var/lib/clamav/$base.new"
        chown clamav:clamav "/var/lib/clamav/$base.new"
        rm -f "/var/lib/clamav/$base.cvd" "/var/lib/clamav/$base.cld"
        mv "/var/lib/clamav/$base.new" "/var/lib/clamav/$base.${new##*.}"
    done
    rm -rf /tmp/newdb'

docker exec "$CONTAINER" clamdscan --reload > /dev/null || fail "демон не принял перезагрузку баз"

echo "Базы установлены: $(docker exec "$CONTAINER" clamdscan --version)"
