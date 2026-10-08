"""Удаление аккаунта пользователя = анонимизация.

Запись в users остаётся, но обезличивается: на неё ссылаются сервисные заявки,
рекламации, заявки на документы и ШУ (это история обслуживания и гарантийных
случаев, она нужна компании, а в Bitrix задачи по ним всё равно остаются).
Личное — телефон, имя, почта, организация, чаты с перепиской и вложениями,
устройства, уведомления, избранное, привязки к проектам и ШУ, служебные заявки
с телефонами и хешами паролей — стирается. Освободившийся номер можно
зарегистрировать заново."""
import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.cabinet_block import CabinetBlock
from app.models.cabinet_user_settings import CabinetUserSettings
from app.models.chat import Chat
from app.models.chat_user_settings import ChatUserSettings
from app.models.device_token import DeviceToken
from app.models.document_access import DocumentAccess
from app.models.message_reaction import MessageReaction
from app.models.messenger_link import MessengerLink
from app.models.notification import Notification
from app.models.notification_settings import NotificationSettings
from app.models.password_reset_request import PasswordResetRequest
from app.models.pending_registration import PendingRegistration
from app.models.phone_change_request import PhoneChangeRequest
from app.models.phone_verification_code import PhoneVerificationCode
from app.models.pinned_chat import PinnedChat
from app.models.registration_request import RegistrationRequest
from app.models.user import User
from app.models.user_cabinet import UserCabinet
from app.models.user_favorite import UserFavorite
from app.models.user_project import UserProject
from app.repositories.auth import RefreshTokenRepository
from app.repositories.chat import MessageRepository

DELETED_USER_NAME = "Удалённый пользователь"

# Строки, которые просто принадлежат пользователю — без него не нужны
_OWNED_BY_USER_ID = (
    DeviceToken, Notification, NotificationSettings, MessengerLink, UserFavorite, UserProject,
    UserCabinet, CabinetUserSettings, ChatUserSettings, PinnedChat, MessageReaction, DocumentAccess,
    CabinetBlock, PendingRegistration, PasswordResetRequest, PhoneChangeRequest,
)


def unusable_password_hash() -> str:
    """Настоящий bcrypt-хеш от случайной строки, которую никто не знает: вход
    невозможен, а проверка пароля не падает, как падала бы на строке-заглушке."""
    return hash_password(secrets.token_urlsafe(32))


async def anonymize_user(session: AsyncSession, user: User) -> None:
    from app.services.chat_service import _schedule_attachment_cleanup

    user_id, phone = user.id, user.phone

    await RefreshTokenRepository(session).revoke_all_for_user(user_id)

    # Чаты вместе с перепиской (в БД — каскадом) и файлами вложений (фоном)
    chat_ids = list((await session.execute(select(Chat.id).where(Chat.user_id == user_id))).scalars().all())
    messages = MessageRepository(session)
    attachment_urls = [url for chat_id in chat_ids for url in await messages.list_all_attachment_urls(chat_id)]
    await session.execute(delete(Chat).where(Chat.user_id == user_id))

    for model in _OWNED_BY_USER_ID:
        await session.execute(delete(model).where(model.user_id == user_id))
    await session.execute(delete(RegistrationRequest).where(RegistrationRequest.created_user_id == user_id))
    if phone:
        await session.execute(delete(PhoneVerificationCode).where(PhoneVerificationCode.phone == phone))

    user.is_active = False
    user.is_verified = False
    user.is_phone_verified = False
    user.phone = None
    user.contact_phone = None
    user.login = f"_deleted_{user_id}"
    user.email = None
    user.full_name = DELETED_USER_NAME
    user.user_type = None
    user.organization_name = None
    user.hashed_password = unusable_password_hash()

    await session.commit()
    _schedule_attachment_cleanup(attachment_urls)
