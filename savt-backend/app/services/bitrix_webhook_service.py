import logging
import re

from app.config import settings
from app.database import AsyncSessionLocal
from app.schemas.chat import MessageCreateIn

_log = logging.getLogger(__name__)

# Только сообщения с этим префиксом из комментария Bitrix-задачи попадают в чат
# заявки в приложении — остальная переписка в задаче остаётся внутри Bitrix
_FORWARD_PREFIX = "/sa"

# Номер объекта — самое первое "слово" в названии сделки, до пробела, например
# "26_138" из "26_138 МГКУП Горсвет_конверт (1-20)". Обязательный префикс (год
# производства, settings.bitrix_production_number_prefix) отсекает сделки с
# номерами других лет/форматов — например "24_004" не попадёт при префиксе "26".
_PRODUCTION_NUMBER_RE = re.compile(
    rf"^({re.escape(settings.bitrix_production_number_prefix)}_\S+)"
)


def _extract(form: dict, *suffixes: str) -> str | None:
    """Bitrix шлёт исходящий вебхук как application/x-www-form-urlencoded с
    вложенными ключами вида data[FIELDS][POST_MESSAGE] — точная вложенность
    зависит от типа события, поэтому ищем по суффиксу ключа, а не по полному имени."""
    for key, value in form.items():
        low = key.lower()
        for suffix in suffixes:
            if low.endswith(suffix.lower()) and value:
                return str(value)
    return None


def verify_token(form: dict) -> bool:
    token = _extract(form, "[application_token]")
    if not token:
        return False
    valid_tokens = {t.strip() for t in settings.bitrix_incoming_webhook_tokens.split(",") if t.strip()}
    return token in valid_tokens


async def handle_task_comment_webhook(form: dict) -> None:
    """Обрабатывает событие добавления комментария к задаче Bitrix (ONTASKCOMMENTADD).
    Пересылает в чат заявки только текст с префиксом _FORWARD_PREFIX — остальное игнорируется.
    Комментарии, которые сами являются нашей же синхронизацией сообщений в Bitrix
    (см. sync_message_to_bitrix), никогда не начинаются с этого префикса, поэтому
    естественным образом не попадают обратно в приложение — отдельной защиты от петли не требуется.
    Сам вебхук несёт только data[FIELDS_AFTER][TASK_ID]/[MESSAGE_ID] — ни текста,
    ни автора в payload нет, оба дотягиваются отдельным запросом
    bitrix_service.get_task_comment (см. аналогичный приём для сделок — get_deal_title)."""
    task_id = _extract(form, "[task_id]")
    message_id = _extract(form, "[message_id]")

    if not task_id or not message_id:
        _log.info("Bitrix webhook: не удалось извлечь task_id/message_id из payload: %s", form)
        return

    from app.services import bitrix_service

    comment = await bitrix_service.get_task_comment(task_id, message_id)
    text = comment.get("POST_MESSAGE") if comment else None
    if not text:
        _log.info("Bitrix webhook: не удалось получить текст комментария task_id=%s message_id=%s", task_id, message_id)
        return

    stripped = text.strip()
    if not stripped.lower().startswith(_FORWARD_PREFIX):
        return
    forwarded_text = stripped[len(_FORWARD_PREFIX):].strip()
    if not forwarded_text:
        return

    from app.repositories.chat import ChatRepository
    from app.repositories.service_request import ServiceRequestRepository
    from app.services.chat_service import ChatService

    async with AsyncSessionLocal() as session:
        req = await ServiceRequestRepository(session).find_by_bitrix_task_id(task_id)
        if req is None:
            _log.info("Bitrix webhook: заявка с bitrix_task_id=%s не найдена", task_id)
            return

        chat = await ChatRepository(session).find_by_service_request(req.id)
        if chat is None:
            _log.info("Bitrix webhook: чат заявки %s не найден", req.id)
            return

        author_id = comment.get("AUTHOR_ID")
        author_name = await bitrix_service.get_user_name(author_id) if author_id else None
        message_text = f"{author_name}: {forwarded_text}" if author_name else forwarded_text

        bitrix_user_id = await bitrix_service.ensure_bitrix_user(session)
        try:
            await ChatService(session).send_message(
                chat.id, bitrix_user_id, MessageCreateIn(text=message_text), sync_to_bitrix=False,
            )
            _log.info("Bitrix webhook: сообщение переслано в чат %s заявки %s: %s", chat.id, req.id, message_text)
        except Exception:
            # Например, чат уже архивирован (заявка закрыта) — комментарий в Bitrix
            # продолжили писать уже после закрытия. Просто не пересылаем, не роняем вебхук.
            _log.exception("Bitrix webhook: не удалось записать сообщение в чат %s", chat.id)


async def handle_task_update_webhook(form: dict) -> None:
    """Обрабатывает ONTASKUPDATE — любое изменение задачи (статус, дедлайн и т.п.).
    Событие не говорит, какое именно поле изменилось, поэтому просто перепроверяем
    текущий статус этой задачи (sync_single_task_status) — мгновенная реакция на
    смену статуса вместо ожидания ближайшего планового опроса (раз в 15 минут)."""
    task_id = _extract(form, "[task_id]")
    if not task_id:
        _log.info("Bitrix task webhook: не удалось извлечь task_id из payload: %s", form)
        return

    from app.services.service_request_service import sync_single_task_status
    await sync_single_task_status(task_id)


def extract_production_number(title: str) -> str | None:
    match = _PRODUCTION_NUMBER_RE.match(title)
    return match.group(1) if match else None


async def upsert_project_from_deal(session, title: str):
    """Создаёт/обновляет Project по номеру, извлечённому из названия сделки Bitrix.
    Возвращает (project, created): project=None, если номер не найден в названии —
    вызывающий код просто пропускает такую сделку, ничего не логируя как ошибку.
    Идемпотентно: повторный вызов с тем же номером не создаёт дубликат
    (см. Project.production_number / ProjectRepository.find_by_production_number).
    Используется и вебхуком (handle_deal_event), и разовым скриптом импорта
    (app/cli.py import-bitrix-deals)."""
    production_number = extract_production_number(title)
    if not production_number:
        return None, False

    from app.repositories.project import ProjectRepository
    from app.services import project_code_service, project_folder_service

    project_repo = ProjectRepository(session)
    existing = await project_repo.find_by_production_number(production_number)

    if existing is not None:
        if existing.name != title:
            existing.name = title
            await session.commit()
            project_folder_service.schedule_folder_sync(existing.id)
        return existing, False

    try:
        unique_code = project_code_service.encrypt_project_code(production_number)
    except project_code_service.ProjectCodeError:
        _log.exception("Не настроен ключ шифрования (BITRIX_PROJECT_CODE_KEY) — проект %s не создан", title)
        return None, False

    project = await project_repo.create(
        name=title, unique_code=unique_code, production_number=production_number,
    )
    await session.flush()
    project.folder_name = project_folder_service.sanitize_folder_name(title)
    await session.commit()
    project_folder_service.schedule_folder_creation(project.id)
    return project, True


async def handle_deal_event(form: dict) -> None:
    """Обрабатывает ONCRMDEALUPDATE — событие несёт только ID сделки, название
    дотягивается отдельным запросом (get_deal_title), дальше см. upsert_project_from_deal."""
    deal_id = _extract(form, "[id]")
    if not deal_id:
        _log.info("Bitrix deal webhook: не удалось извлечь ID сделки из payload: %s", form)
        return

    from app.services import bitrix_service

    title = await bitrix_service.get_deal_title(deal_id)
    if not title:
        _log.info("Bitrix deal webhook: не удалось получить название сделки %s", deal_id)
        return

    async with AsyncSessionLocal() as session:
        project, created = await upsert_project_from_deal(session, title)

    if project is None:
        _log.info("Bitrix deal webhook: номер проекта не найден в названии сделки '%s'", title)
    elif created:
        _log.info("Bitrix deal webhook: создан проект id=%s ('%s')", project.id, title)
