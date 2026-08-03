import asyncio
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.cabinets import Cabinet
from app.models.document import Document
from app.models.project import Project
from app.repositories.cabinet import CabinetRepository
from app.repositories.document import DocumentRepository
from app.repositories.project import ProjectRepository
from app.services.qr_service import generate_qr
from app.services.upload_service import UPLOAD_ROOT

logger = logging.getLogger(__name__)

# Шаблонная структура папок проекта (см. README) — пути относительно корня проекта
TEMPLATE_SUBFOLDERS = [
    "_Проект",
    "_Программа/SCADA",
    "_Программа/ПЛК",
    "_Программа/HMI",
    "_Маркировка/Облако",
    "_Маркировка/Coral",
    "_Маркировка/Wago",
    "_Руководство",
    "Фото",
    "_Доп.информация",
    "Переписка",
]

_QR_FILENAME = "QR.png"
_INVALID_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
# Синхронизация продолжается ещё столько дней после истечения гарантии — если
# гарантию продлят позже, синхронизация возобновится сама при следующем прогоне
_GRACE_DAYS = 7


def sanitize_folder_name(name: str) -> str:
    cleaned = _INVALID_CHARS_RE.sub("_", name).strip().rstrip(".")
    return (cleaned or "Без_названия")[:150]


def _mirrored_filename(title: str, file_url: str | None) -> str:
    ext = Path(file_url).suffix if file_url else ""
    return sanitize_folder_name(title) + ext


def is_sync_eligible(cabinets: list[Cabinet], project: Project | None = None) -> bool:
    """Синхронизировать ли папку проекта. Порядок источников гарантии:

    1) project.warranty_ends_at — если администратор её проставил, решает она:
       это прямой ответ человека, он важнее вычисленного;
    2) иначе — крайняя (MAX) дата окончания среди гарантий ШУ проекта, как было
       до появления проектной гарантии;
    3) если нет ни того, ни другого — синхронизируем (не блокируем свежий проект,
       которому ещё не привязали ШУ и не заполнили гарантию).

    Пока проектная гарантия не заполнена, поведение ровно прежнее."""
    deadline = None
    if project is not None and project.warranty_ends_at is not None:
        deadline = project.warranty_ends_at
    else:
        ends = [c.warranty_ends_at for c in cabinets if c.warranty_ends_at is not None]
        if ends:
            deadline = max(ends)

    if deadline is None:
        return True
    return deadline + timedelta(days=_GRACE_DAYS) >= datetime.now(timezone.utc)


async def _parent_root_path(project: Project, project_repo: ProjectRepository) -> Path:
    ancestors = await project_repo.get_ancestors(project.id)
    root = Path(settings.project_folders_root)
    for ancestor in ancestors:
        root = root / (ancestor.folder_name or sanitize_folder_name(ancestor.name))
    return root


async def _project_root_path(project: Project, project_repo: ProjectRepository) -> Path:
    parent_root = await _parent_root_path(project, project_repo)
    return parent_root / (project.folder_name or sanitize_folder_name(project.name))


async def _ensure_structure(root: Path) -> None:
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    for sub in TEMPLATE_SUBFOLDERS:
        await asyncio.to_thread((root / sub).mkdir, parents=True, exist_ok=True)


async def mirror_document_to_nas(root: Path, document: Document) -> None:
    if not document.file_url:
        return
    src = UPLOAD_ROOT / document.file_url.removeprefix("/static/")
    if not await asyncio.to_thread(src.exists):
        return
    dest = root / _mirrored_filename(document.title, document.file_url)
    await asyncio.to_thread(shutil.copy2, src, dest)


async def remove_document_from_nas(root: Path, title: str, file_url: str | None) -> None:
    dest = root / _mirrored_filename(title, file_url)
    if await asyncio.to_thread(dest.exists):
        await asyncio.to_thread(dest.unlink)


async def write_project_qr(root: Path, project: Project) -> None:
    """Кладёт QR-код проекта (тот же savt://project/{unique_code}, что и в приложении)
    картинкой в _Маркировка — печатается и клеится на объект физически."""
    image_bytes = generate_qr(f"savt://project/{project.unique_code}")
    dest = root / "_Маркировка" / _QR_FILENAME
    await asyncio.to_thread(dest.write_bytes, image_bytes)


async def create_project_folder_structure(project: Project, project_repo: ProjectRepository) -> None:
    if not settings.project_folders_root:
        logger.info("project_folders_root не настроен — пропускаю создание папки (project_id=%s)", project.id)
        return
    root = await _project_root_path(project, project_repo)
    await _ensure_structure(root)
    await write_project_qr(root, project)


async def sync_project_folder(session: AsyncSession, project: Project) -> None:
    """Структурная сверка + сверка документов проекта с их зеркалом на NAS.
    Переименовывает корневую папку, если project.name разошёлся с folder_name,
    досоздаёт недостающие подпапки шаблона, докопировает отсутствующие в корне
    файлы документов проекта (Document.project_id)."""
    if not settings.project_folders_root:
        return

    project_repo = ProjectRepository(session)
    parent_root = await _parent_root_path(project, project_repo)
    new_name = sanitize_folder_name(project.name)

    if project.folder_name and project.folder_name != new_name:
        old_path = parent_root / project.folder_name
        new_path = parent_root / new_name
        if await asyncio.to_thread(old_path.exists) and not await asyncio.to_thread(new_path.exists):
            await asyncio.to_thread(old_path.rename, new_path)
        project.folder_name = new_name
    elif not project.folder_name:
        project.folder_name = new_name

    root = parent_root / project.folder_name
    await _ensure_structure(root)

    qr_path = root / "_Маркировка" / _QR_FILENAME
    if not await asyncio.to_thread(qr_path.exists):
        await write_project_qr(root, project)

    doc_repo = DocumentRepository(session)
    docs, _ = await doc_repo.list_admin(project_id=project.id, limit=10_000)
    for doc in docs:
        dest = root / _mirrored_filename(doc.title, doc.file_url)
        if not await asyncio.to_thread(dest.exists):
            await mirror_document_to_nas(root, doc)

    project.folder_synced_at = datetime.now(timezone.utc)
    await session.commit()


async def sync_all_project_folders() -> None:
    """Ночной прогон: синхронизирует только проекты, у которых гарантия ШУ ещё
    актуальна (+ неделя запаса) — см. is_sync_eligible."""
    if not settings.project_folders_root:
        return
    async with AsyncSessionLocal() as session:
        project_repo = ProjectRepository(session)
        cabinet_repo = CabinetRepository(session)
        projects = await project_repo.list_all_active()
        for project in projects:
            cabinets = await cabinet_repo.list_by_project(project.id)
            if not is_sync_eligible(cabinets, project):
                continue
            try:
                await sync_project_folder(session, project)
            except Exception:
                logger.exception("Не удалось синхронизировать папку проекта %s", project.id)


# --- fire-and-forget обёртки для вызова из request-хендлеров сервисов ---
# (своя сессия — вызываются после коммита основного запроса, не должны его блокировать/ронять)

def schedule_folder_creation(project_id: int) -> None:
    async def _task():
        async with AsyncSessionLocal() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_id(project_id)
            if project is None:
                return
            try:
                await create_project_folder_structure(project, project_repo)
            except Exception:
                logger.exception("Не удалось создать папку для проекта %s", project_id)
    asyncio.create_task(_task())


def schedule_folder_sync(project_id: int) -> None:
    async def _task():
        async with AsyncSessionLocal() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_id(project_id)
            if project is None:
                return
            try:
                await sync_project_folder(session, project)
            except Exception:
                logger.exception("Не удалось синхронизировать папку проекта %s", project_id)
    asyncio.create_task(_task())


def schedule_document_mirror(project_id: int, document_id: int) -> None:
    async def _task():
        async with AsyncSessionLocal() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_id(project_id)
            doc = await DocumentRepository(session).get_by_id(document_id)
            if project is None or doc is None:
                return
            try:
                root = await _project_root_path(project, project_repo)
                await mirror_document_to_nas(root, doc)
            except Exception:
                logger.exception("Не удалось зеркалить документ %s в папку проекта %s на NAS", document_id, project_id)
    asyncio.create_task(_task())


def schedule_document_removal(project_id: int, title: str, file_url: str | None) -> None:
    async def _task():
        async with AsyncSessionLocal() as session:
            project_repo = ProjectRepository(session)
            project = await project_repo.get_by_id(project_id)
            if project is None:
                return
            try:
                root = await _project_root_path(project, project_repo)
                await remove_document_from_nas(root, title, file_url)
            except Exception:
                logger.exception("Не удалось убрать зеркало документа из папки проекта %s на NAS", project_id)
    asyncio.create_task(_task())
