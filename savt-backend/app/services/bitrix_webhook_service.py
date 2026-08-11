import logging
import re
from datetime import datetime, timezone

from app.config import settings
from app.database import AsyncSessionLocal
from app.schemas.chat import MessageCreateIn

_log = logging.getLogger(__name__)

# Только сообщения с этим префиксом из комментария Bitrix-задачи попадают в чат
# заявки в приложении — остальная переписка в задаче остаётся внутри Bitrix
_FORWARD_PREFIX = "/sa"

# Номер проекта — две цифры года, подчёркивание и дальше номер: "26_138".
# Год не фиксирован: в работе одновременно бывают 25-е, 26-е и 27-е номера,
# поэтому раньше стоявший здесь единственный префикс отсекал живые сделки.
# Ограничить список годов можно настройкой bitrix_production_years.
_PRODUCTION_NUMBER_RE = re.compile(r"^(\d{2}_\S+)")


def _allowed_years() -> set[str]:
    return {y.strip() for y in settings.bitrix_production_years.split(",") if y.strip()}


def _first_filled(deal: dict, codes: str):
    """Первое непустое значение среди перечисленных через запятую полей.

    Нужно для фактической даты отгрузки: в портале живут два поля сразу (старое и
    новое), и пока идёт переезд часть сделок заполнена по-старому, часть по-новому.

    Часть полей объявлена множественными и приходит списком (пустое значение у них
    не "", а []) — берём первый элемент. Без этого дата из множественного поля
    молча не разбиралась бы: на вход парсеру уходил бы список, а не строка."""
    for code in (c.strip() for c in (codes or "").split(",")):
        if not code:
            continue
        value = deal.get(code)
        if isinstance(value, list):
            value = value[0] if value else None
        if value not in (None, "", False):
            return value
    return None


def _parse_bitrix_datetime(raw) -> datetime | None:
    """Bitrix отдаёт даты в ISO с часовым поясом ("2026-08-15T03:00:00+03:00"),
    но у полей типа date встречается и "15.08.2026" — разбираем оба варианта."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    _log.warning("Bitrix: не удалось разобрать дату %r", raw)
    return None


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
    ни автора в payload нет. В новой карточке задачи комментарии физически хранятся
    в чате (IM), поэтому текст дотягивается через tasks.task.get (chat_id) +
    im.dialog.messages.get — старый task.commentitem.get не работает (см. README)."""
    task_id = _extract(form, "[task_id]")
    message_id = _extract(form, "[message_id]")

    if not task_id or not message_id:
        _log.info("Bitrix webhook: не удалось извлечь task_id/message_id из payload: %s", form)
        return

    from app.services import bitrix_service

    dialog_id = await bitrix_service.get_task_chat_id(task_id)
    if not dialog_id:
        _log.info("Bitrix webhook: не удалось получить chat_id задачи %s", task_id)
        return

    comment = await bitrix_service.get_dialog_message(dialog_id, message_id)
    text = comment.get("text") if comment else None
    if not text:
        _log.info("Bitrix webhook: не удалось получить текст сообщения task_id=%s dialog_id=%s message_id=%s", task_id, dialog_id, message_id)
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

        # Отправитель в чате отображается как "Ася" (см. bitrix_service._INCOMING_USER_NAME),
        # чтобы визуально не выглядело как прямая переписка с Bitrix — сам текст ниже
        # явно поясняет, что ответ на самом деле от оператора, а не от бота
        author_id = comment.get("author_id") or comment.get("AUTHOR_ID")
        author_name = await bitrix_service.get_user_name(author_id) if author_id else None
        prefix = f"Ответ от оператора {author_name}" if author_name else "Ответ от оператора"
        message_text = f"{prefix}: {forwarded_text}"

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
    ID задачи здесь приходит просто как data[FIELDS_AFTER][ID] (в отличие от
    ONTASKCOMMENTADD, где тот же смысл несёт TASK_ID) — событие не говорит, какое
    именно поле изменилось, поэтому просто перепроверяем текущий статус этой задачи
    (sync_single_task_status) — мгновенная реакция вместо ожидания планового опроса."""
    task_id = _extract(form, "[id]")
    if not task_id:
        _log.info("Bitrix task webhook: не удалось извлечь id задачи из payload: %s", form)
        return

    from app.services.service_request_service import sync_single_task_status
    await sync_single_task_status(task_id)


def extract_production_number(deal: dict) -> str | None:
    """Номер проекта из сделки: сначала из выделенного поля, при пустом — из
    названия, как было раньше.

    Поле надёжнее: название сделки правят руками, и любая опечатка в начале
    раньше означала, что проект не создастся или не найдётся. Откат на название
    оставлен, чтобы ничего не потерялось, пока поле заполнено не у всех сделок."""
    raw = None
    if settings.bitrix_field_production_number:
        raw = str(deal.get(settings.bitrix_field_production_number) or "").strip()
    if not raw:
        raw = str(deal.get("TITLE") or "").strip()

    match = _PRODUCTION_NUMBER_RE.match(raw)
    if not match:
        return None
    number = match.group(1)

    years = _allowed_years()
    if years and number[:2] not in years:
        return None
    return number


async def _apply_deal_fields(project, deal: dict) -> None:
    """Переносит в проект поля, которыми владеет Bitrix. Гарантию не трогает —
    она наша и в CRM её нет."""
    from app.services import bitrix_service

    project.shipment_planned_at = _parse_bitrix_datetime(
        _first_filled(deal, settings.bitrix_field_shipment_planned)
    )
    project.shipment_actual_at = _parse_bitrix_datetime(
        _first_filled(deal, settings.bitrix_field_shipment_actual)
    )

    company_id = str(deal.get("COMPANY_ID") or "").strip()
    if company_id and company_id != "0":
        project.bitrix_company_id = company_id
        name = await bitrix_service.get_company_name(company_id)
        # При сбое запроса имя не затираем — лучше показать устаревшее, чем пустое
        if name:
            project.company_name = name
    else:
        project.bitrix_company_id = None
        project.company_name = None


async def _sync_contacts(session, project, deal_id: str) -> None:
    """Синхронизирует контактных лиц с заменой набора: убранный из сделки контакт
    удаляется и у нас, иначе в карточке со временем повиснут уволившиеся.

    Если Bitrix недоступен, набор не трогаем вовсе — иначе разовый сетевой сбой
    стирал бы все контакты проекта."""
    from app.services import bitrix_service
    from app.repositories.project import ProjectContactRepository

    contact_ids = await bitrix_service.get_deal_contact_ids(deal_id)
    if contact_ids is None:
        _log.warning("Bitrix: не удалось получить контакты сделки %s — набор оставлен как был", deal_id)
        return

    contacts = []
    for order, contact_id in enumerate(contact_ids):
        data = await bitrix_service.get_contact(contact_id)
        if data is None:
            _log.warning("Bitrix: контакт %s не прочитался, пропускаю", contact_id)
            continue
        data["sort_order"] = order
        contacts.append(data)

    await ProjectContactRepository(session).replace_for_project(project.id, contacts)


async def upsert_project_from_deal(session, deal: dict):
    """Создаёт/обновляет Project по сделке Bitrix.
    Возвращает (project, created): project=None, если номер проекта не определился —
    вызывающий код просто пропускает такую сделку, ничего не логируя как ошибку.
    Идемпотентно: повторный вызов с тем же номером не создаёт дубликат
    (см. Project.production_number / ProjectRepository.find_by_production_number).
    Используется и вебхуком (handle_deal_event), и разовым скриптом импорта
    (app/cli.py import-bitrix-deals)."""
    production_number = extract_production_number(deal)
    if not production_number:
        return None, False

    from app.repositories.project import ProjectRepository
    from app.services import project_code_service, project_folder_service

    title = str(deal.get("TITLE") or "").strip() or production_number
    deal_id = str(deal.get("ID") or "").strip()

    project_repo = ProjectRepository(session)
    # Ищем и среди мягко удалённых: проект — собственность Bitrix, пока жива
    # сделка — жив и он. Удаление в приложении (DELETE /admin/projects/{id})
    # не должно быть окончательным, пока сделку не закрыли и в CRM.
    existing = await project_repo.find_by_production_number_any(production_number)

    if existing is not None:
        resurrected = existing.deleted_at is not None
        if resurrected:
            existing.deleted_at = None
            _log.info(
                "Bitrix: проект %s (%r) был удалён в приложении — воскрешён по сделке %s",
                existing.id, title, deal_id,
            )
        renamed = existing.name != title
        if renamed:
            existing.name = title
        await _apply_deal_fields(existing, deal)
        await session.commit()
        if renamed or resurrected:
            project_folder_service.schedule_folder_sync(existing.id)
        if deal_id:
            await _sync_contacts(session, existing, deal_id)
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
    await _apply_deal_fields(project, deal)
    await session.commit()
    project_folder_service.schedule_folder_creation(project.id)
    if deal_id:
        await _sync_contacts(session, project, deal_id)
    return project, True


async def handle_deal_event(form: dict) -> None:
    """Обрабатывает ONCRMDEALADD/ONCRMDEALUPDATE — событие несёт только ID сделки,
    сама сделка дотягивается запросом (get_deal), дальше см. upsert_project_from_deal."""
    deal_id = _extract(form, "[id]")
    if not deal_id:
        _log.info("Bitrix deal webhook: не удалось извлечь ID сделки из payload: %s", form)
        return

    from app.services import bitrix_service

    deal = await bitrix_service.get_deal(deal_id)
    if not deal:
        _log.info("Bitrix deal webhook: не удалось получить сделку %s", deal_id)
        return

    async with AsyncSessionLocal() as session:
        project, created = await upsert_project_from_deal(session, deal)

    title = deal.get("TITLE")
    if project is None:
        _log.info("Bitrix deal webhook: номер проекта не определился для сделки '%s'", title)
    elif created:
        _log.info("Bitrix deal webhook: создан проект id=%s ('%s')", project.id, title)
