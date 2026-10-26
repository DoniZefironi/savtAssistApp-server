<#
.SYNOPSIS
  Обновляет базы вирусов антивируса ClamAV на сервере с этого компьютера.

.DESCRIPTION
  Сервер обновлений ClamAV закрыт для адресов из некоторых стран, в том числе для нашего
  сервера, поэтому контейнер clamav сам обновиться не может. Этот скрипт запускается на
  компьютере, которому сервер обновлений открыт, и делает три вещи:
    1. скачивает свежие базы (docker-контейнер clamav, freshclam; базы хранятся в томе
       savt-clamav-db, поэтому после первого раза скачиваются только небольшие обновления);
    2. упаковывает main, daily и bytecode в архив;
    3. отправляет архив на сервер по SSH, где clamav-install-db.sh проверяет подписи баз,
       подменяет их в контейнере и перезагружает антивирус.
  Если базы с прошлой отправки не менялись, ничего не отправляется. Журнал: update.log в
  папке %LOCALAPPDATA%\savt-clamav. О том, что базы на сервере устарели, сообщает ночная
  проверка backup-check.sh (сообщением в Telegram).

  Нужны: запущенный Docker Desktop и SSH-ключ, привязанный на сервере к clamav-install-db.sh
  (настройка описана в README, раздел «Антивирус для загруженных файлов»).

.PARAMETER SkipUpload
  Только скачать и упаковать, на сервер ничего не отправлять (проверка на этом компьютере).

.PARAMETER Force
  Отправить, даже если базы не менялись с прошлой отправки.
#>
param(
    [string]$Server = "10.1.0.208",
    [string]$User = "admin101",
    [int]$Port = 22,
    [string]$Key = (Join-Path $env:USERPROFILE ".ssh\savt_clamav"),
    [string]$Image = "clamav/clamav:1.4",
    [switch]$SkipUpload,
    [switch]$Force
)

# Ошибки внешних программ проверяются по коду выхода, а не исключениями: иначе PowerShell 5.1
# считает ошибкой любую строку, которую docker или ssh вывели в stderr
$ErrorActionPreference = "Continue"

$work = Join-Path $env:LOCALAPPDATA "savt-clamav"
$null = New-Item -ItemType Directory -Force -Path $work
$logFile = Join-Path $work "update.log"
$archive = Join-Path $work "clamav-db.tgz"
$stateFile = Join-Path $work "last-uploaded.sha256"
$volume = "savt-clamav-db"

if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 1MB)) {
    Move-Item -Force $logFile "$logFile.old"
}

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Stop-WithError([string]$Message) {
    Write-Log "ОШИБКА: $Message"
    exit 1
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) { Stop-WithError "Docker не запущен или недоступен" }

# 1. Свежие базы. Новый том docker наполняется базами из образа, дальше freshclam докачивает
# только разницу
Write-Log "Скачиваю обновления баз вирусов"
$fresh = & docker run --rm -v "${volume}:/var/lib/clamav" $Image freshclam --stdout 2>&1
$freshExit = $LASTEXITCODE
($fresh | Select-Object -Last 6) | ForEach-Object { Write-Log "  freshclam: $_" }
if ($freshExit -ne 0) { Stop-WithError "freshclam завершился с кодом $freshExit (сервер обновлений ClamAV недоступен и с этого компьютера?)" }

# 2. Совпадают ли базы с теми, что уже отправлены на сервер
$hash = (& docker run --rm --entrypoint sh -v "${volume}:/db:ro" $Image -c "cd /db && sha256sum main.c?d daily.c?d bytecode.c?d | sha256sum | cut -d' ' -f1") | Select-Object -First 1
if ($LASTEXITCODE -ne 0 -or -not $hash) { Stop-WithError "не удалось посчитать контрольную сумму баз" }
$hash = $hash.Trim()
$lastHash = if (Test-Path $stateFile) { (Get-Content $stateFile -Raw).Trim() } else { "" }
if (-not $Force -and -not $SkipUpload -and $hash -eq $lastHash) {
    Write-Log "Базы не менялись с прошлой отправки, отправлять нечего"
    exit 0
}

# 3. Архив
Remove-Item -Force -ErrorAction SilentlyContinue $archive
& docker run --rm --entrypoint sh -v "${volume}:/db:ro" -v "${work}:/out" $Image -c "cd /db && tar czf /out/clamav-db.tgz main.c?d daily.c?d bytecode.c?d"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $archive)) { Stop-WithError "не удалось упаковать базы" }
$sizeMb = [math]::Round((Get-Item $archive).Length / 1MB, 1)
Write-Log "Архив готов: $sizeMb МБ"

if ($SkipUpload) {
    Write-Log "Режим SkipUpload: на сервер ничего не отправляю, архив: $archive"
    exit 0
}

# 4. Отправка. Архив бинарный, а конвейер PowerShell портит двоичные данные, поэтому
# перенаправление файла в ssh делает cmd через временный .cmd-файл
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { Stop-WithError "в Windows не установлен клиент OpenSSH (ssh)" }
if (-not (Test-Path $Key)) { Stop-WithError "нет ключа $Key (см. README, раздел «Антивирус для загруженных файлов»)" }

$uploadCmd = Join-Path $work "upload.cmd"
$line = 'ssh -i "{0}" -p {4} -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 {1}@{2} install < "{3}" 2>&1' -f $Key, $User, $Server, $archive, $Port
Set-Content -Path $uploadCmd -Value "@echo off`r`n$line`r`nexit /b %errorlevel%" -Encoding ASCII

Write-Log "Отправляю базы на сервер $Server"
$result = & $uploadCmd
$uploadExit = $LASTEXITCODE
$result | ForEach-Object { Write-Log "  сервер: $_" }
Remove-Item -Force -ErrorAction SilentlyContinue $archive, $uploadCmd
if ($uploadExit -ne 0) { Stop-WithError "сервер не принял базы (код $uploadExit)" }

Set-Content -Path $stateFile -Value $hash -Encoding ASCII
Write-Log "Готово"
