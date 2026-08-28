import logging

import httpx

from app.config import settings

_log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
# JWT кэшируется в памяти процесса между вызовами — коротко живущий, добывается
# логином служебного аккаунта (см. _login), не статический токен из .env.
_access_token: str | None = None
# Внешний сервис отдаёт refresh-токен и как заголовок Refresh-token (виден в
# CORS exposedHeaders их Program.cs), и, вероятно, как куку — httpx хранит куку
# в самом клиенте автоматически, а вот заголовок нужно ловить и слать самим,
# на случай если /api/User/refresh ждёт его именно так, а не только из куки.
_refresh_token: str | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # Один клиент на весь процесс — для connection reuse и (в теории) для
        # refresh-куки, которую httpx хранит в самом клиенте. На практике кука
        # refreshToken приходит с флагом Secure, а base_url тут — plain HTTP,
        # так что httpx её обратно не отправит (Secure = только по HTTPS) —
        # _refresh() будет молча проваливаться, и _authorized_request просто
        # перелогинивается заново при 401. Это ок, не баг: относительно редкий
        # лишний запрос на каждое истечение токена, а не проблема вовсе.
        _client = httpx.AsyncClient(base_url=settings.sim_service_base_url, timeout=10)
    return _client


def _configured() -> bool:
    return bool(settings.sim_service_login and settings.sim_service_password)


def _extract_token(resp: httpx.Response) -> str | None:
    token = resp.headers.get("authorization")
    if not token:
        return None
    if token.lower().startswith("bearer "):
        token = token[7:]
    return token or None


def _capture_tokens(resp: httpx.Response) -> str | None:
    """Забирает access-токен из заголовка authorization, и заодно, если
    прислали, обновляет закэшированный refresh-токен из заголовка
    Refresh-token (не только из куки, см. комментарий у _refresh_token)."""
    global _refresh_token
    refresh = resp.headers.get("refresh-token")
    if refresh:
        _refresh_token = refresh
    return _extract_token(resp)


async def _login() -> str | None:
    global _access_token
    if not _configured():
        return None
    try:
        resp = await _get_client().post(
            "/api/User/login",
            params={"login": settings.sim_service_login, "password": settings.sim_service_password},
        )
    except httpx.RequestError as e:
        _log.warning("sim_service login: сетевая ошибка %s", e)
        return None
    if not resp.is_success:
        _log.warning("sim_service login: HTTP %s %s", resp.status_code, resp.text[:200])
        return None
    _access_token = _capture_tokens(resp)
    if _access_token is None:
        _log.warning("sim_service login: ответ 2xx, но заголовок authorization пуст")
    return _access_token


async def _refresh() -> str | None:
    global _access_token
    headers = {"Refresh-token": _refresh_token} if _refresh_token else {}
    try:
        resp = await _get_client().get("/api/User/refresh", headers=headers)
    except httpx.RequestError as e:
        _log.warning("sim_service refresh: сетевая ошибка %s", e)
        return None
    if not resp.is_success:
        return None
    _access_token = _capture_tokens(resp)
    return _access_token


async def _authorized_request(method: str, path: str, **kwargs) -> httpx.Response | None:
    """Один HTTP-вызов с JWT: логинится при первом обращении, при 401 сперва
    пробует освежить токен через refresh-куку, а если и это не помогло —
    логинится заново. Возвращает None, если сервис не настроен, недоступен
    по сети, либо так и не удалось авторизоваться."""
    global _access_token
    if not _configured():
        return None
    if _access_token is None and await _login() is None:
        return None

    client = _get_client()

    async def _do() -> httpx.Response | None:
        try:
            return await client.request(method, path, headers={"Authorization": f"Bearer {_access_token}"}, **kwargs)
        except httpx.RequestError as e:
            _log.warning("sim_service %s %s: сетевая ошибка %s", method, path, e)
            return None

    resp = await _do()
    if resp is not None and resp.status_code == 401:
        if await _refresh() is None and await _login() is None:
            return resp
        resp = await _do()
    return resp


async def get_sim(sim_id: int) -> dict | None:
    """Одна SIM-карта по id (GET /api/Sim/{id}) — для показа статуса/IP/телефона
    на странице ШУ. None — SIM не найдена, сервис недоступен/не настроен или
    авторизоваться не удалось; вызывающий код должен деградировать тихо (не
    ронять страницу ШУ из-за недоступности стороннего приложения), см.
    CabinetService.get."""
    resp = await _authorized_request("GET", f"/api/Sim/{sim_id}")
    if resp is None or resp.status_code == 404:
        return None
    if not resp.is_success:
        _log.warning("sim_service get_sim(%s): HTTP %s %s", sim_id, resp.status_code, resp.text[:200])
        return None
    return resp.json()


async def list_sims(page: int = 1, limit: int = 20) -> tuple[list[dict], int]:
    """Страница всех SIM без фильтра (GET /api/Sim) — для обзорного списка в
    админке при выборе SIM для привязки к ШУ. Возвращает ([], 0) при недоступности."""
    resp = await _authorized_request("GET", "/api/Sim", params={"page": page, "limit": limit})
    if resp is None or not resp.is_success:
        if resp is not None:
            _log.warning("sim_service list_sims: HTTP %s %s", resp.status_code, resp.text[:200])
        return [], 0
    data = resp.json()
    return data.get("items") or [], data.get("total") or 0


async def search_sims(
    page: int = 1,
    limit: int = 20,
    name: str | None = None,
    phone: str | None = None,
    serial_number: str | None = None,
    ip: str | None = None,
) -> tuple[list[dict], int]:
    """Поиск SIM по подстроке (POST /api/Sim/search) — для выбора SIM при
    привязке к ШУ в админке. Поля, оставленные пустыми, в фильтр не попадают
    (внешний сервис ищет только по непустым полям)."""
    body = {
        k: v for k, v in {
            "name": name, "phone": phone, "serialNumber": serial_number, "ip": ip,
        }.items() if v
    }
    resp = await _authorized_request("POST", "/api/Sim/search", params={"page": page, "limit": limit}, json=body)
    if resp is None or not resp.is_success:
        if resp is not None:
            _log.warning("sim_service search_sims: HTTP %s %s", resp.status_code, resp.text[:200])
        return [], 0
    data = resp.json()
    return data.get("items") or [], data.get("total") or 0
