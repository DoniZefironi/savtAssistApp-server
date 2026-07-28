import logging

from app.config import settings
from app.database import AsyncSessionLocal
from app.schemas.chat import MessageCreateIn

_log = logging.getLogger(__name__)

# Только сообщения с этим префиксом из комментария Bitrix-задачи попадают в чат
# заявки в приложении — остальная переписка в задаче остаётся внутри Bitrix
_FORWARD_PREFIX = "/all"


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
    return bool(settings.bitrix_incoming_webhook_token) and token == settings.bitrix_incoming_webhook_token


async def handle_task_comment_webhook(form: dict) -> None:
    """Обрабатывает событие добавления комментария к задаче Bitrix (ONTASKCOMMENTADD).
    Пересылает в чат заявки только текст с префиксом /all — остальное игнорируется.
    Комментарии, которые сами являются нашей же синхронизацией сообщений в Bitrix
    (см. sync_message_to_bitrix), никогда не начинаются с /all, поэтому естественным
    образом не попадают обратно в приложение — отдельной защиты от петли не требуется."""
    task_id = _extract(form, "[task_id]")
    text = _extract(form, "[post_message]", "[message]", "[comment]")
    author_id = _extract(form, "[author_id]")

    if not task_id or not text:
        _log.info("Bitrix webhook: не удалось извлечь task_id/текст комментария из payload: %s", form)
        return

    stripped = text.strip()
    if not stripped.lower().startswith(_FORWARD_PREFIX):
        return
    forwarded_text = stripped[len(_FORWARD_PREFIX):].strip()
    if not forwarded_text:
        return

    from app.repositories.chat import ChatRepository
    from app.repositories.service_request import ServiceRequestRepository
    from app.services import bitrix_service
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

        author_name = await bitrix_service.get_user_name(author_id) if author_id else None
        message_text = f"{author_name}: {forwarded_text}" if author_name else forwarded_text

        bitrix_user_id = await bitrix_service.ensure_bitrix_user(session)
        try:
            await ChatService(session).send_message(
                chat.id, bitrix_user_id, MessageCreateIn(text=message_text), sync_to_bitrix=False,
            )
        except Exception:
            # Например, чат уже архивирован (заявка закрыта) — комментарий в Bitrix
            # продолжили писать уже после закрытия. Просто не пересылаем, не роняем вебхук.
            _log.exception("Bitrix webhook: не удалось записать сообщение в чат %s", chat.id)
