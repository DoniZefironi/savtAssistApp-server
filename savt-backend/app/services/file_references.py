"""Где в базе хранятся ссылки на файлы из /uploads.

Один и тот же файл может держать несколько записей: клиент вправе приложить к своему сообщению
ссылку на файл, который уже лежит в документе, фото или вложении базы знаний. Поэтому файл,
чья запись удаляется, стирать с диска можно только если на него больше ничего не ссылается
(см. unreferenced_urls). Новая колонка со ссылкой на файл обязана попасть в file_url_columns:
это проверяет tests/test_file_references.py."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def file_url_columns() -> list:
    # Импорты внутри функции: модели тянут за собой весь граф приложения
    from app.models.cabinet_addition_request import CabinetAdditionRequest
    from app.models.cabinet_photo import CabinetPhoto
    from app.models.chat_user_settings import ChatUserSettings
    from app.models.document import Document
    from app.models.kb_article_attachment import KbArticleAttachment
    from app.models.message_attchment import MessageAttachment
    from app.models.reclamation import Reclamation
    from app.models.reclamation_attachment import ReclamationAttachment

    return [
        CabinetAdditionRequest.photo_url,
        CabinetPhoto.url,
        ChatUserSettings.wallpaper_url,
        Document.file_url,
        KbArticleAttachment.file_url,
        MessageAttachment.file_url,
        Reclamation.confirmation_file_url,
        ReclamationAttachment.file_url,
    ]


async def referenced_urls(session: AsyncSession) -> set[str]:
    """Все ссылки на файлы, которые сейчас есть в базе, одним проходом по каждой таблице. Нужна
    уборке файлов без ссылок (orphan_files): там кандидатов сотни, и проверять каждого отдельным
    запросом на большой таблице вложений слишком дорого."""
    from app.core.signed_urls import strip_signature

    used: set[str] = set()
    for column in file_url_columns():
        rows = await session.execute(select(column).where(column.is_not(None)).distinct())
        # В базе ссылки голые, но на случай подписанной ссылки сравниваем по пути
        used.update(strip_signature(url) or url for (url,) in rows.all())
    return used


async def unreferenced_urls(session: AsyncSession, urls: list[str]) -> list[str]:
    """Из urls оставляет те, на которые не ссылается ни одна запись. Все колонки проверяются
    по одному запросу на таблицу сразу для всех ссылок: проверка каждой ссылки по отдельности
    на большой таблице вложений означала бы по полному просмотру таблицы на каждый файл."""
    wanted = list(dict.fromkeys(url for url in urls if url))
    if not wanted:
        return []

    still_used: set[str] = set()
    for column in file_url_columns():
        rows = await session.execute(select(column).where(column.in_(wanted)).distinct())
        still_used.update(url for (url,) in rows.all())
    return [url for url in wanted if url not in still_used]
