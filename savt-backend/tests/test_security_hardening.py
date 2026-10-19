"""Защита сервера: проверка слабых настроек, отключение документации API в проде,
раздача /static приложением только по действующей подписи. Сами подписи и их
проверку (verify_signature) проверяют test_uploads.py и test_api_odds_and_ends.py."""
import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount

from app import cli
from app.config import Settings, settings
from app.core.security import create_access_token
from app.core.signed_urls import accept_issued_url, sign_url, strip_signature, was_issued
from app.core.startup_checks import config_problems, docs_enabled, is_production
from app.core.static_files import SignedStaticFiles

STRONG = "k" * 40


def _settings(**overrides) -> Settings:
    base = dict(
        database_url="postgresql+asyncpg://u:p@db/x", jwt_secret_key=STRONG, static_link_secret=STRONG,
        cors_origins="https://helper.savt.by,http://10.1.0.208:8080", app_env="prod",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --- настройки ---

def test_a_sound_production_configuration_has_no_problems():
    assert config_problems(_settings()) == []


@pytest.mark.parametrize("overrides,fragment", [
    ({"jwt_secret_key": "short"}, "JWT_SECRET_KEY"),
    ({"jwt_secret_key": "change_me_to_a_long_random_secret_xxxxxxxxxxxxxxxx"}, "JWT_SECRET_KEY"),
    ({"static_link_secret": ""}, "STATIC_LINK_SECRET не задан"),
    ({"static_link_secret": "change_me_to_a_long_random_secret"}, "STATIC_LINK_SECRET не задан"),
    ({"static_link_secret": "k" * 12}, "STATIC_LINK_SECRET короче"),
    ({"cors_origins": "*"}, "CORS_ORIGINS"),
    ({"cors_origins": "https://a.by, *"}, "CORS_ORIGINS"),
    ({"cors_origins": "https://helper.savt.by/app/"}, "https://helper.savt.by/app/ — адрес с путём"),
    ({"cors_origins": "https://helper.savt.by/"}, "https://helper.savt.by/ — адрес с путём"),
    ({"app_env": "dev"}, "APP_ENV"),
])
def test_each_weak_setting_is_reported(overrides, fragment):
    problems = config_problems(_settings(**overrides))

    assert len(problems) == 1 and fragment in problems[0]


def test_all_problems_are_listed_at_once():
    assert len(config_problems(_settings(jwt_secret_key="x", static_link_secret="", cors_origins="*", app_env="dev"))) == 4


def test_environment_names():
    assert is_production("prod") and is_production(" Production ") and not is_production("dev") and not is_production("")
    assert docs_enabled("dev") is True and docs_enabled("prod") is False and docs_enabled("production") is False


def test_check_config_command(monkeypatch, capsys):
    monkeypatch.setattr("app.config.settings", _settings())
    cli._check_config()
    assert "в порядке" in capsys.readouterr().out

    monkeypatch.setattr("app.config.settings", _settings(cors_origins="*"))
    with pytest.raises(SystemExit) as exit_info:
        cli._check_config()
    assert exit_info.value.code == 1 and "CORS_ORIGINS" in capsys.readouterr().out


# --- документация API ---

def test_the_application_follows_the_environment_for_its_docs():
    from app.main import app

    assert (app.docs_url, app.redoc_url, app.openapi_url) == (
        ("/docs", "/redoc", "/openapi.json") if docs_enabled(settings.app_env) else (None, None, None)
    )


# --- раздача /static ---

@pytest.fixture
def static_client(tmp_path, monkeypatch):
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "a.txt").write_text("секретное содержимое", encoding="utf-8")
    monkeypatch.setattr(settings, "static_link_secret", STRONG)
    app = Starlette(routes=[Mount("/static", SignedStaticFiles(directory=str(tmp_path)), name="static")])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


async def test_file_is_served_only_with_a_valid_signature(static_client):
    async with static_client as client:
        valid = await client.get(sign_url("/static/photos/a.txt"))
        unsigned = await client.get("/static/photos/a.txt")
        other_file = await client.get(sign_url("/static/photos/a.txt").replace("a.txt", "b.txt"))
        expired = await client.get(sign_url("/static/photos/a.txt", ttl_seconds=-5))
        missing_signed = await client.get(sign_url("/static/photos/gone.txt"))

    assert valid.status_code == 200 and valid.text == "секретное содержимое"
    assert unsigned.status_code == other_file.status_code == expired.status_code == 403
    assert missing_signed.status_code == 404               # подпись верна, файла нет


async def test_without_a_secret_files_stay_open_for_local_runs(static_client, monkeypatch):
    monkeypatch.setattr(settings, "static_link_secret", "")

    async with static_client as client:
        assert (await client.get("/static/photos/a.txt")).status_code == 200


async def test_the_real_application_checks_signatures_on_static(api, monkeypatch):
    monkeypatch.setattr(settings, "static_link_secret", STRONG)

    assert (await api.get("/static/photos/anything.jpg")).status_code == 403
    assert (await api.get(sign_url("/static/photos/anything.jpg"))).status_code == 404


def test_the_application_mounts_the_signed_variant():
    from app.main import app

    mounts = [route for route in app.routes if getattr(route, "name", None) == "static"]
    assert len(mounts) == 1 and isinstance(mounts[0].app, SignedStaticFiles)


# --- ссылки на файлы, которые присылает клиент ---

def test_only_signatures_issued_by_us_are_genuine(monkeypatch):
    monkeypatch.setattr(settings, "static_link_secret", STRONG)
    path = "/static/documents/a.pdf"

    assert was_issued(sign_url(path))
    assert was_issued(sign_url(path, ttl_seconds=-3600))             # срок вышел, но ссылку выдали мы
    assert not was_issued(path)                                      # без подписи
    assert not was_issued(None) and not was_issued("")
    assert not was_issued(sign_url("/static/documents/b.pdf").replace("b.pdf", "a.pdf"))   # подпись от другого файла
    assert not was_issued(f"{path}?md5=AAAA&expires=1")
    assert not was_issued(f"{path}?md5=AAAA&expires=не-число")


def test_without_a_secret_any_link_counts_as_issued(monkeypatch):
    monkeypatch.setattr(settings, "static_link_secret", "")

    assert was_issued("/static/documents/a.pdf") and accept_issued_url("/static/documents/a.pdf")


def test_input_links_are_stripped_and_only_static_paths_need_a_signature(monkeypatch):
    monkeypatch.setattr(settings, "static_link_secret", STRONG)
    path = "/static/documents/a.pdf"

    assert accept_issued_url(sign_url(path)) == path
    for value in (None, "", "color:#112233", "https://example.com/a.png"):
        assert accept_issued_url(value) == strip_signature(value)     # не наши ссылки проходят как раньше
    with pytest.raises(ValueError, match="недействительна"):
        accept_issued_url(path)


@pytest.fixture
def quiet_chat(monkeypatch):
    from app.services import chat_service, push_service

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(chat_service, "spawn", lambda coro, **kw: coro.close())
    monkeypatch.setattr(push_service, "send_push", noop)
    monkeypatch.setattr(settings, "static_link_secret", STRONG)


def _attachment(url):
    return {"attachments": [{"file_url": url, "file_name": "a.pdf", "file_size_bytes": 10, "mime_type": "application/pdf"}]}


async def test_a_message_accepts_only_file_links_that_we_issued(api, make_user, make_chat, quiet_chat):
    user = await make_user()
    chat = await make_chat(user, "support", bot_active=False)
    headers = {"Authorization": f"Bearer {create_access_token(user_id=user.id, role='user')}"}
    url = f"/chats/{chat.id}/messages"
    path = "/static/documents/0123456789abcdef.pdf"

    unsigned = await api.post(url, headers=headers, json=_attachment(path))
    forged = await api.post(url, headers=headers, json=_attachment(f"{path}?md5=AAAA&expires=9999999999"))
    ours = await api.post(url, headers=headers, json=_attachment(sign_url(path)))
    expired = await api.post(url, headers=headers, json=_attachment(sign_url(path, ttl_seconds=-3600)))

    assert unsigned.status_code == forged.status_code == 422
    assert ours.status_code == expired.status_code == 201           # оффлайн-очередь с просроченной ссылкой работает
    assert ours.json()["attachments"][0]["file_url"].startswith(path)


async def test_wallpaper_accepts_colors_and_links_we_issued_but_not_foreign_static_paths(
    api, make_user, make_chat, quiet_chat,
):
    user = await make_user()
    chat = await make_chat(user, "support", bot_active=False)
    headers = {"Authorization": f"Bearer {create_access_token(user_id=user.id, role='user')}"}
    url = f"/chats/{chat.id}/wallpaper"

    colour = await api.patch(url, headers=headers, json={"wallpaper_url": "color:#112233"})
    ours = await api.patch(url, headers=headers, json={"wallpaper_url": sign_url("/static/photos/ab12.jpg")})
    foreign = await api.patch(url, headers=headers, json={"wallpaper_url": "/static/photos/other-users-file.jpg"})
    reset = await api.patch(url, headers=headers, json={"wallpaper_url": None})

    assert colour.status_code == ours.status_code == reset.status_code == 200
    assert foreign.status_code == 422
