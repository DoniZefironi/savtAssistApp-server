"""Внешний сервис SIM-карт: вход служебным аккаунтом, обновление токена при 401,
получение, список и поиск SIM, тихая деградация при недоступности, а также админский
поиск по нескольким полям сразу. Сеть подменена транспортом httpx."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import settings
from app.core.security import create_access_token
from app.services import sim_service


@pytest.fixture
def sims(monkeypatch):
    """Фальшивый SimApi: токен tok-1 после логина, tok-2 после refresh; всё остальное — 401."""
    state = SimpleNamespace(
        requests=[], valid={"tok-1"}, login_status=200, login_token="Bearer tok-1", login_extra={},
        refresh_ok=True, refresh_token_after="tok-2", sim={"id": "s1", "serialNumber": "SN", "phone": "+375291112233", "ip": "10.0.0.1"},
        list_body={"items": [{"id": "s1"}], "total": 1}, fail_network=False, status_for_sim=None,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        path = request.url.path
        if state.fail_network:
            raise httpx.ConnectError("нет сети")
        if path == "/api/User/login":
            headers = {"authorization": state.login_token, **state.login_extra} if state.login_token else dict(state.login_extra)
            return httpx.Response(state.login_status, headers=headers, text="login")
        if path == "/api/User/refresh":
            if not state.refresh_ok:
                return httpx.Response(401)
            state.valid.add(state.refresh_token_after)
            return httpx.Response(200, headers={"authorization": f"bearer {state.refresh_token_after}"})
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in state.valid:
            return httpx.Response(401)
        if state.status_for_sim:
            return httpx.Response(state.status_for_sim, text="boom")
        if request.method == "GET" and path.startswith("/api/Sim/"):
            return httpx.Response(200, json=state.sim)
        return httpx.Response(200, json=state.list_body)

    monkeypatch.setattr(sim_service, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sim.test"))
    monkeypatch.setattr(sim_service, "_access_token", None)
    monkeypatch.setattr(sim_service, "_refresh_token", None)
    monkeypatch.setattr(settings, "sim_service_login", "robot")
    monkeypatch.setattr(settings, "sim_service_password", "secret")
    return state


def _logins(state):
    return [r for r in state.requests if r.url.path == "/api/User/login"]


async def test_without_credentials_nothing_is_requested(sims, monkeypatch):
    monkeypatch.setattr(settings, "sim_service_password", "")

    assert await sim_service.get_sim("s1") is None
    assert await sim_service.list_sims() == ([], 0)
    assert await sim_service.search_sims(name="x") == ([], 0)
    assert sims.requests == []


async def test_first_call_logs_in_and_the_token_is_reused(sims):
    first = await sim_service.get_sim("s1")
    second = await sim_service.get_sim("s1")

    assert first == second == sims.sim
    assert len(_logins(sims)) == 1
    login = _logins(sims)[0]
    assert dict(login.url.params) == {"login": "robot", "password": "secret"}
    assert sims.requests[1].headers["authorization"] == "Bearer tok-1"


@pytest.mark.parametrize("failure", ["http-error", "no-header", "network"])
async def test_failed_login_means_no_data(sims, failure):
    if failure == "http-error":
        sims.login_status = 500
    elif failure == "no-header":
        sims.login_token = None
    else:
        sims.fail_network = True

    assert await sim_service.get_sim("s1") is None
    assert [r.url.path for r in sims.requests if r.url.path != "/api/User/login"] == []


async def test_expired_token_is_refreshed_and_the_call_repeated(sims):
    await sim_service.get_sim("s1")
    sims.valid = set()                      # токен протух
    sims.valid.add("tok-2")                 # refresh выдаст tok-2

    sims.valid.discard("tok-2")
    result = await sim_service.get_sim("s1")

    assert result == sims.sim
    paths = [r.url.path for r in sims.requests]
    assert "/api/User/refresh" in paths and len(_logins(sims)) == 1


async def test_failed_refresh_falls_back_to_a_new_login(sims):
    await sim_service.get_sim("s1")
    sims.valid = {"tok-new"}
    sims.login_token = "Bearer tok-new"
    sims.refresh_ok = False

    assert await sim_service.get_sim("s1") == sims.sim
    assert len(_logins(sims)) == 2


async def test_when_nothing_helps_the_call_gives_up_quietly(sims):
    await sim_service.get_sim("s1")
    sims.valid = set()
    sims.refresh_ok = False
    sims.login_status = 500

    assert await sim_service.get_sim("s1") is None


async def test_refresh_token_header_is_remembered_and_sent_back(sims):
    sims.login_extra = {"refresh-token": "ref-1"}
    await sim_service.get_sim("s1")
    sims.valid = set()

    await sim_service.get_sim("s1")

    refresh = next(r for r in sims.requests if r.url.path == "/api/User/refresh")
    assert refresh.headers["refresh-token"] == "ref-1"


def test_token_extraction():
    make = lambda value: httpx.Response(200, headers={"authorization": value} if value else {})   # noqa: E731

    assert sim_service._extract_token(make("Bearer abc")) == "abc"
    assert sim_service._extract_token(make("bearer abc")) == "abc"
    assert sim_service._extract_token(make("raw-token")) == "raw-token"
    assert sim_service._extract_token(make(None)) is None
    assert sim_service._extract_token(make("Bearer ")) is None


async def test_missing_unavailable_and_broken_sims_give_none(sims):
    sims.status_for_sim = 404
    assert await sim_service.get_sim("s1") is None
    sims.status_for_sim = 500
    assert await sim_service.get_sim("s1") is None
    sims.status_for_sim = None
    sims.fail_network = True
    sim_service._access_token = "tok-1"
    assert await sim_service.get_sim("s1") is None


async def test_list_and_search(sims):
    page = await sim_service.list_sims(page=2, limit=5)
    found = await sim_service.search_sims(page=1, limit=10, name="Насос", phone="", serial_number="SN-7")

    assert page == ([{"id": "s1"}], 1) and found == ([{"id": "s1"}], 1)
    list_request = next(r for r in sims.requests if r.method == "GET" and r.url.path == "/api/Sim")
    assert dict(list_request.url.params) == {"page": "2", "limit": "5"}
    search_request = next(r for r in sims.requests if r.url.path == "/api/Sim/search")
    assert json.loads(search_request.content) == {"name": "Насос", "serialNumber": "SN-7"}   # пустые поля не уходят


async def test_list_and_search_degrade_to_empty(sims):
    sims.list_body = {"items": None, "total": None}
    assert await sim_service.list_sims() == ([], 0)

    sims.status_for_sim = 500
    assert await sim_service.list_sims() == ([], 0)
    assert await sim_service.search_sims(name="x") == ([], 0)


# --- админский поиск ---

def _auth(user, role):
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id, role=role)}"}


@pytest.fixture
def sim_api(monkeypatch):
    calls = SimpleNamespace(search=[], listing=[])
    data = {
        "name": ([{"id": "a", "serialNumber": "S-A"}, {"id": "b"}], 2),
        "phone": ([{"id": "b"}, {"id": "c", "phone": "+375291112233"}], 2),
        "serial": ([{"id": "a"}, {"id": None}], 2),
    }

    async def search_sims(page=1, limit=20, name=None, phone=None, serial_number=None, ip=None):
        calls.search.append(dict(page=page, limit=limit, name=name, phone=phone, serial_number=serial_number, ip=ip))
        if ip:
            return [{"id": "narrow"}], 1
        if name and not phone and not serial_number:
            return data["name"]
        if phone and not name:
            return data["phone"]
        if serial_number and not name:
            return data["serial"]
        return [{"id": "narrow"}], 1

    async def list_sims(page=1, limit=20):
        calls.listing.append((page, limit))
        return [{"id": "x1"}, {"id": "x2"}], 42

    monkeypatch.setattr(sim_service, "search_sims", search_sims)
    monkeypatch.setattr(sim_service, "list_sims", list_sims)
    monkeypatch.setattr(settings, "sim_service_frontend_url", "http://sim.front")
    return calls


async def test_plain_listing_is_paginated(api, make_user, sim_api):
    operator = await make_user("operator")

    response = await api.get("/admin/sim", params={"page": 2, "size": 2}, headers=_auth(operator, "operator"))

    body = response.json()
    assert response.status_code == 200 and [i["id"] for i in body["items"]] == ["x1", "x2"] and body["total"] == 42
    assert body["items"][0]["sim_url"] == "http://sim.front" and sim_api.listing == [(2, 2)]


async def test_free_text_searches_every_field_and_merges_without_duplicates(api, make_user, sim_api):
    admin = await make_user("admin")

    response = await api.get("/admin/sim", params={"name": "насос", "size": 2, "page": 1}, headers=_auth(admin, "admin"))
    second = await api.get("/admin/sim", params={"name": "насос", "size": 2, "page": 2}, headers=_auth(admin, "admin"))

    assert [i["id"] for i in response.json()["items"]] == ["a", "b"]
    assert [i["id"] for i in second.json()["items"]] == ["c"] and response.json()["total"] == 3
    assert {tuple((k, v) for k, v in call.items() if v and k not in ("page", "limit")) for call in sim_api.search[:3]} == {
        (("name", "насос"),), (("phone", "насос"),), (("serial_number", "насос"),),
    }


async def test_explicit_fields_do_a_narrow_search(api, make_user, sim_api):
    admin = await make_user("admin")

    response = await api.get("/admin/sim", params={"phone": "+375291112233", "ip": "10.0.0.1"}, headers=_auth(admin, "admin"))

    assert [i["id"] for i in response.json()["items"]] == ["narrow"]
    assert sim_api.search == [dict(page=1, limit=20, name=None, phone="+375291112233", serial_number=None, ip="10.0.0.1")]


async def test_sim_search_is_closed_to_clients(api, make_user, sim_api):
    client = await make_user()

    assert (await api.get("/admin/sim", headers=_auth(client, "user"))).status_code == 403
    assert (await api.get("/admin/sim")).status_code in (401, 403)
