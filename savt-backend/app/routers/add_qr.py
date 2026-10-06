from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_session
from app.repositories.cabinet import CabinetRepository
from app.repositories.project import ProjectRepository

router = APIRouter(tags=["add-qr"])

# Публичная страница для QR, отсканированного обычной камерой телефона, а не
# внутри приложения — сам этот адрес и зашит в QR (см. app/routers/qr.py,
# app/services/project_folder_service.py). Сканирование ВНУТРИ приложения
# по-прежнему идёт через POST /projects|cabinets/add-by-qr напрямую, эта
# страница в том сценарии вообще не открывается.
#
# Если приложение уже установлено и настроен Android App Link на наш домен —
# Android перехватит переход сюда сам, страница даже не откроется. Если
# перехвата нет (домен не верифицирован) — страница пытается открыть
# приложение через intent://, а если и это не сработало (приложения правда
# нет) — остаётся кнопка скачать APK.
_PAGE_TEMPLATE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SAVT Assist</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#F1F3F5;color:#14181C;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;box-sizing:border-box}}
  .card{{background:#fff;border-radius:16px;padding:32px 28px;max-width:360px;width:100%;text-align:center;
        box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  h1{{font-size:18px;margin:0 0 8px}}
  p{{font-size:14px;color:#5B6570;line-height:1.5;margin:0 0 20px}}
  a.btn{{display:block;background:#E8720C;color:#fff;text-decoration:none;font-weight:600;
        padding:14px;border-radius:10px;font-size:15px}}
  .muted{{font-size:12.5px;color:#8A93A0;margin-top:16px}}
</style>
</head><body>
<div class="card">
  <h1>{title}</h1>
  <p>{subtitle}</p>
  {button}
  <p class="muted">Откройте эту ссылку на телефоне, если ещё не открыли</p>
</div>
{intent_script}
</body></html>"""


def _render(title: str, subtitle: str, intent_url: str | None) -> str:
    if settings.apk_download_url:
        button = f'<a class="btn" href="{escape(settings.apk_download_url)}">Скачать приложение</a>'
    else:
        button = '<p class="muted">Приложение пока не опубликовано — обратитесь к администратору</p>'
    intent_script = (
        f'<script>window.location.href={intent_url!r};</script>' if intent_url else ""
    )
    return _PAGE_TEMPLATE.format(
        title=escape(title), subtitle=escape(subtitle), button=button, intent_script=intent_script,
    )


def _intent_url(path: str) -> str | None:
    if not settings.android_package_name:
        return None
    fallback = f"{settings.public_base_url.rstrip('/')}{path}"
    return (
        f"intent://{path.lstrip('/')}#Intent;scheme=savt;"
        f"package={settings.android_package_name};"
        f"S.browser_fallback_url={fallback};end"
    )


@router.get("/add/project/{unique_code}", response_class=HTMLResponse)
async def add_project_landing(unique_code: str, session: AsyncSession = Depends(get_session)):
    project = await ProjectRepository(session).find_by_code(unique_code)
    name = project.name if project is not None else None
    html = _render(
        title=f"Проект «{name}»" if name else "Проект не найден",
        subtitle=(
            "Открываем в приложении SAVT Assist…" if name
            else "Такого проекта нет, или он больше не действует"
        ),
        intent_url=_intent_url(f"/add/project/{unique_code}") if name else None,
    )
    return HTMLResponse(html)


@router.get("/add/cabinet/{unique_code}", response_class=HTMLResponse)
async def add_cabinet_landing(unique_code: str, session: AsyncSession = Depends(get_session)):
    cabinet = await CabinetRepository(session).find_by_code(unique_code)
    name = (cabinet.admin_internal_name or cabinet.object_number) if cabinet is not None else None
    html = _render(
        title=f"ШУ «{name}»" if name else "ШУ не найден",
        subtitle=(
            "Открываем в приложении SAVT Assist…" if name
            else "Такого ШУ нет, или он больше не действует"
        ),
        intent_url=_intent_url(f"/add/cabinet/{unique_code}") if name else None,
    )
    return HTMLResponse(html)
