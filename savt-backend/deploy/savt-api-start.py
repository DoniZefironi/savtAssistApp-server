from __future__ import annotations

import http.client
import json
import socket
import subprocess
import time


def api_ready() -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=3)
    try:
        connection.request("GET", "/backend/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        health = json.loads(response.read(4096))
        return isinstance(health, dict) and health.get("app") == "ok" and health.get("db") is True
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        connection.close()


def nas_host(config: dict) -> str | None:
    for mount in config["services"]["api"].get("volumes", []):
        if mount["target"] != "/mnt/projects" or mount["type"] != "volume":
            continue
        options = config["volumes"][mount["source"]].get("driver_opts", {})
        if options.get("type") != "cifs":
            return None
        device = options.get("device", "").replace("\\", "/")
        host, _, share = device.removeprefix("//").partition("/")
        if not device.startswith("//") or not host or not share:
            raise ValueError("Укажите NAS_SHARE_DEVICE в формате //host/share")
        return host
    return None


def main() -> int:
    if api_ready():
        print("API и PostgreSQL уже доступны через Nginx")
        return 0

    # Docker может вывести параметры монтирования CIFS, включая пароль.
    try:
        result = subprocess.run(
            ["docker", "compose", "config", "--no-env-resolution", "--format", "json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )
        config = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        print("Не удалось прочитать конфигурацию Docker Compose")
        return 1

    try:
        host = nas_host(config)
    except ValueError as error:
        print(error)
        return 1
    if host:
        try:
            with socket.create_connection((host, 445), timeout=3):
                pass
        except OSError:
            print(f"SMB на {host}:445 пока недоступен")
            return 1

    try:
        subprocess.run(
            [
                "docker", "compose", "up", "-d", "--no-deps", "--no-build",
                "--no-recreate", "--pull", "never", "api",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=40,
        )
    except (OSError, subprocess.SubprocessError):
        print("Docker Compose не запустил API. Подробности в журнале docker.service")
        return 1

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if api_ready():
            print("API и PostgreSQL доступны через Nginx")
            return 0
        time.sleep(2)

    print("API ещё не прошёл проверку /backend/health через Nginx")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
