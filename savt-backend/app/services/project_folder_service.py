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
from app.services.upload_service import UPLOAD_ROOT, save_local_file

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

_CHAT_TITLES = {
    "project": "Чат проекта",
    "cabinet": "Чат ШУ",
    "support": "Чат поддержки",
}

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

    await import_new_files_from_nas(session, project, root, docs)

    # Чаты, где с прошлой сверки ничего не писали, перечитывать не нужно
    since = project.folder_synced_at

    # Уровень проекта: его собственные фото и переписка
    await export_photos(session, root, project_id=project.id)
    await export_chats(session, root, project_id=project.id, since=since)

    # Уровень ШУ: у каждого шкафа проекта своя папка с тем же шаблоном
    for cabinet in await CabinetRepository(session).list_by_project(project.id):
        cabinet_root = root / _cabinet_folder_name(cabinet)
        await _ensure_structure(cabinet_root)
        await export_photos(session, cabinet_root, cabinet_id=cabinet.id)
        await export_chats(session, cabinet_root, cabinet_id=cabinet.id, since=since)

    project.folder_synced_at = datetime.now(timezone.utc)
    await session.commit()


def _cabinet_folder_name(cabinet: Cabinet) -> str:
    """Папка ШУ внутри папки проекта: "29_099 ШУ-18К" либо просто номер объекта."""
    parts = [cabinet.object_number or f"ШУ-{cabinet.id}"]
    if cabinet.admin_internal_name:
        parts.append(cabinet.admin_internal_name)
    return sanitize_folder_name(" ".join(parts))


async def export_photos(
    session: AsyncSession, root: Path, *,
    cabinet_id: int | None = None, project_id: int | None = None,
) -> int:
    """Раскладывает фотографии в подпапку «Фото». Уже скопированные пропускает —
    файл на NAS не перезаписывается, чтобы не трогать то, что могли переименовать
    или дополнить вручную."""
    from app.models.cabinet_photo import CabinetPhoto
    from sqlalchemy import select

    stmt = select(CabinetPhoto).order_by(CabinetPhoto.sort_order, CabinetPhoto.id)
    stmt = stmt.where(
        CabinetPhoto.project_id == project_id if project_id is not None
        else CabinetPhoto.cabinet_id == cabinet_id
    )
    photos = list((await session.execute(stmt)).scalars().all())
    if not photos:
        return 0

    dest_dir = root / "Фото"
    await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)

    copied = 0
    for index, photo in enumerate(photos, start=1):
        src = UPLOAD_ROOT / (photo.url or "").removeprefix("/static/")
        if not await asyncio.to_thread(src.is_file):
            continue
        # Номер в начале удерживает порядок сортировки в проводнике
        stem = sanitize_folder_name(photo.caption) if photo.caption else f"Фото {index}"
        dest = dest_dir / f"{index:03d} {stem}{src.suffix}"
        if await asyncio.to_thread(dest.exists):
            continue
        try:
            await asyncio.to_thread(shutil.copyfile, src, dest)
            copied += 1
        except OSError:
            logger.exception("Не удалось скопировать фото %s в %s", photo.id, dest)
    return copied


def _format_message(msg, sender_name: str | None, attachments: list) -> str:
    stamp = msg.created_at.strftime("%d.%m.%Y %H:%M") if msg.created_at else "—"
    author = sender_name or "—"
    if msg.deleted_at:
        return f"[{stamp}] {author}: (сообщение удалено)"

    lines = [f"[{stamp}] {author}: {msg.text or ''}".rstrip()]
    for att in attachments:
        if att.attachment_type == "location":
            lines.append(f"    [геолокация: {att.latitude}, {att.longitude}]")
        else:
            lines.append(f"    [вложение: {att.file_name or 'без имени'}]")
    return "\n".join(lines)


async def export_chats(
    session: AsyncSession, root: Path, *,
    cabinet_id: int | None = None, project_id: int | None = None,
    since: datetime | None = None, only_chat_id: int | None = None,
) -> int:
    """Выгружает переписку в подпапку «Переписка»: по файлу на чат, вложения —
    в «Переписка/вложения» рядом.

    Стенограммы перезаписываются на каждой сверке: переписка растёт, и дописывать
    хвост было бы сложнее и ненадёжнее, чем просто собрать файл заново. Вложения
    наоборот копируются один раз — они неизменны.

    since — не перечитывать чаты, где с этого момента ничего не писали (обычно
    это folder_synced_at проекта). Файл всё равно собирается заново, если его
    ещё нет на диске: пропускать можно только то, что уже выгружено.

    only_chat_id — выгрузить один конкретный чат (закрытие заявки), не трогая
    соседние стенограммы."""
    from app.models.chat import Chat
    from app.models.message import Message
    from app.models.message_attchment import MessageAttachment
    from app.models.user import User
    from sqlalchemy import select

    stmt = select(Chat).where(
        Chat.project_id == project_id if project_id is not None
        else Chat.cabinet_id == cabinet_id
    )
    if only_chat_id is not None:
        stmt = stmt.where(Chat.id == only_chat_id)
    chats = list((await session.execute(stmt)).scalars().all())
    if not chats:
        return 0

    dest_dir = root / "Переписка"
    att_dir = dest_dir / "вложения"
    await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)

    exported = 0
    for chat in chats:
        # Имя файла считаем до чтения сообщений: по нему решаем, можно ли
        # вообще пропустить чат
        owner = await session.get(User, chat.user_id)
        owner_name = (owner.full_name or owner.phone or f"id{chat.user_id}") if owner else f"id{chat.user_id}"
        title = _CHAT_TITLES.get(chat.chat_type, "Чат")
        if chat.chat_type == "service_request" and chat.service_request_id:
            title = f"Заявка {chat.service_request_id}"
        dest = dest_dir / f"{sanitize_folder_name(f'{title} — {owner_name}')}.txt"

        # Ничего не писали с прошлой сверки и файл уже есть — перечитывать
        # переписку незачем. На больших проектах это основная экономия.
        if (
            since is not None
            and chat.last_message_at is not None
            and chat.last_message_at <= since
            and await asyncio.to_thread(dest.exists)
        ):
            continue

        rows = (await session.execute(
            select(Message, User)
            .outerjoin(User, User.id == Message.sender_id)
            .where(Message.chat_id == chat.id)
            .order_by(Message.id)
        )).all()
        if not rows:
            continue

        msg_ids = [m.id for m, _ in rows]
        atts_by_msg: dict[int, list] = {mid: [] for mid in msg_ids}
        for att in (await session.execute(
            select(MessageAttachment).where(MessageAttachment.message_id.in_(msg_ids))
        )).scalars().all():
            atts_by_msg[att.message_id].append(att)

        header = [
            f"{title} — {owner_name}",
            f"Выгружено: {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} UTC",
            "",
        ]
        body = [_format_message(m, u.full_name if u else None, atts_by_msg[m.id]) for m, u in rows]

        try:
            await asyncio.to_thread(dest.write_text, "\n".join(header + body), "utf-8")
            exported += 1
        except OSError:
            logger.exception("Не удалось записать стенограмму чата %s", chat.id)
            continue

        # Вложения кладём рядом, с id сообщения в имени — иначе одноимённые
        # файлы из разных сообщений затирали бы друг друга
        for mid in msg_ids:
            for att in atts_by_msg[mid]:
                if not att.file_url:
                    continue
                src = UPLOAD_ROOT / att.file_url.removeprefix("/static/")
                if not await asyncio.to_thread(src.is_file):
                    continue
                name = sanitize_folder_name(att.file_name or src.name)
                att_dest = att_dir / f"{mid}_{name}"
                if await asyncio.to_thread(att_dest.exists):
                    continue
                await asyncio.to_thread(att_dir.mkdir, parents=True, exist_ok=True)
                try:
                    await asyncio.to_thread(shutil.copyfile, src, att_dest)
                except OSError:
                    logger.exception("Не удалось скопировать вложение %s", att.id)
    return exported


async def import_new_files_from_nas(
    session: AsyncSession, project: Project, root: Path, known_docs: list[Document],
) -> int:
    """Обратное направление: файл положили в папку проекта напрямую — заводим
    на него документ в приложении. Возвращает число подхваченных файлов.

    Сканируется только корень папки проекта: именно туда кладёт зеркалирование,
    и именно там человек оставляет файл «чтобы появился в приложении». Вложенные
    папки шаблона (_Проект, _Программа, Фото и т.п.) не трогаем — файлы там
    разложены осмысленно, и сваливать их в плоский список документов означало бы
    потерять эту структуру."""
    if not settings.project_folders_root:
        return 0

    # Имя зеркала для документов, загруженных через приложение, плюс фактическое
    # имя для тех, что уже были подхвачены отсюда раньше
    known_names = {_mirrored_filename(d.title, d.file_url) for d in known_docs}
    known_names |= {d.nas_filename for d in known_docs if d.nas_filename}

    try:
        entries = await asyncio.to_thread(lambda: sorted(root.iterdir(), key=lambda p: p.name))
    except OSError:
        logger.exception("Не удалось прочитать папку проекта %s", project.id)
        return 0

    doc_repo = DocumentRepository(session)
    imported = 0
    for entry in entries:
        if await asyncio.to_thread(entry.is_dir):
            continue
        if entry.name in known_names or entry.name.startswith("~$"):
            continue
        try:
            info = await asyncio.to_thread(save_local_file, entry)
        except OSError:
            logger.exception("Не удалось скопировать %s в uploads", entry)
            continue

        await doc_repo.create(
            project_id=project.id,
            cabinet_id=None,
            title=entry.stem or entry.name,
            doc_type=info.doc_type,
            file_url=info.url,
            file_size_bytes=info.file_size_bytes,
            mime_type=info.mime_type,
            requires_approval=False,
            # Запоминаем имя как оно есть на диске — иначе следующая сверка
            # снова сочтёт файл новым (см. комментарий у Document.nas_filename)
            nas_filename=entry.name,
        )
        imported += 1
        logger.info("Подхвачен файл из папки проекта %s: %s", project.id, entry.name)

    if imported:
        await session.commit()
    return imported


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


def schedule_cabinet_folder(cabinet_id: int) -> None:
    """Папка ШУ внутри папки проекта — с тем же шаблоном подпапок.

    При отвязке или переносе ШУ в другой проект старая папка НЕ переносится и не
    удаляется: в ней могут лежать файлы, положенные людьми вручную. Новая просто
    создаётся на новом месте, старую при необходимости переносят руками — так же,
    как со сменой родителя у проекта."""
    async def _task():
        if not settings.project_folders_root:
            return
        async with AsyncSessionLocal() as session:
            cabinet = await CabinetRepository(session).get_by_id(cabinet_id)
            if cabinet is None or cabinet.project_id is None:
                return
            project = await ProjectRepository(session).get_by_id(cabinet.project_id)
            if project is None:
                return
            try:
                root = await _project_root_path(project, ProjectRepository(session))
                await _ensure_structure(root / _cabinet_folder_name(cabinet))
            except Exception:
                logger.exception("Не удалось создать папку ШУ %s на NAS", cabinet_id)
    asyncio.create_task(_task())


def schedule_request_chat_export(chat_id: int) -> None:
    """Выгружает стенограмму чата заявки в папку сразу при её закрытии.

    Закрытие — естественная точка архивации: чат становится read-only, значит
    выгруженный файл уже не устареет. Ждать ночной сверки незачем, а на самой
    сверке этот чат будет пропущен как неизменившийся."""
    async def _task():
        if not settings.project_folders_root:
            return
        async with AsyncSessionLocal() as session:
            from app.models.chat import Chat
            chat = await session.get(Chat, chat_id)
            if chat is None:
                return

            project_repo = ProjectRepository(session)
            cabinet = None
            if chat.cabinet_id is not None:
                cabinet = await CabinetRepository(session).get_by_id(chat.cabinet_id)
                project_id = cabinet.project_id if cabinet else None
            else:
                project_id = chat.project_id
            if project_id is None:
                return
            project = await project_repo.get_by_id(project_id)
            if project is None:
                return

            try:
                root = await _project_root_path(project, project_repo)
                if cabinet is not None:
                    root = root / _cabinet_folder_name(cabinet)
                await _ensure_structure(root)
                await export_chats(
                    session, root,
                    cabinet_id=chat.cabinet_id, project_id=chat.project_id,
                    only_chat_id=chat_id,
                )
            except Exception:
                logger.exception("Не удалось выгрузить стенограмму чата %s при закрытии заявки", chat_id)
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
