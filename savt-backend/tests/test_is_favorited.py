"""is_favorited в ответах KB / FAQ / документов — клиенту не нужно отдельно
ходить в GET /favorites и сверять id. Гость (user_id=None) и чужое избранное
всегда дают False.
"""
from app.models.faq_category import FaqCategory
from app.models.faq_entry import FaqEntry
from app.models.kbarticle import KbArticle
from app.models.kbcategory import KbCategory
from app.repositories.favorite import FavoriteRepository
from app.services.document_service import UserDocumentService
from app.services.faq_service import FaqEntryService
from app.services.kb_service import KbArticleService


async def _kb_article(db_session, n: int) -> KbArticle:
    category = KbCategory(name=f"Категория {n}", slug=f"kb-cat-{n}")
    db_session.add(category)
    await db_session.flush()
    article = KbArticle(
        category_id=category.id, title=f"Статья {n}", slug=f"kb-article-{n}",
        content="Текст", is_published=True,
    )
    db_session.add(article)
    await db_session.flush()
    return article


async def _faq_entry(db_session, n: int) -> FaqEntry:
    category = FaqCategory(name=f"FAQ категория {n}")
    db_session.add(category)
    await db_session.flush()
    entry = FaqEntry(category_id=category.id, question=f"Вопрос номер {n}?", answer="Ответ", is_published=True)
    db_session.add(entry)
    await db_session.flush()
    return entry


# --- KB ---

async def test_kb_list_marks_only_users_own_favorites(db_session, make_user):
    user = await make_user()
    other = await make_user()
    liked = await _kb_article(db_session, 1)
    plain = await _kb_article(db_session, 2)
    await FavoriteRepository(db_session).add(user.id, "kb_article", liked.id)
    await FavoriteRepository(db_session).add(other.id, "kb_article", plain.id)

    page = await KbArticleService(db_session).list_articles(None, None, None, user_id=user.id)

    flags = {item.id: item.is_favorited for item in page.items}
    assert flags[liked.id] is True
    assert flags[plain.id] is False


async def test_kb_list_for_guest_is_never_favorited(db_session, make_user):
    user = await make_user()
    article = await _kb_article(db_session, 3)
    await FavoriteRepository(db_session).add(user.id, "kb_article", article.id)

    page = await KbArticleService(db_session).list_articles(None, None, None, user_id=None)

    assert all(item.is_favorited is False for item in page.items)


async def test_kb_detail_reflects_favorite(db_session, make_user):
    user = await make_user()
    article = await _kb_article(db_session, 4)
    svc = KbArticleService(db_session)

    assert (await svc.get_detail(article.id, user_id=user.id)).is_favorited is False
    await FavoriteRepository(db_session).add(user.id, "kb_article", article.id)
    assert (await svc.get_detail(article.id, user_id=user.id)).is_favorited is True
    assert (await svc.get_detail(article.id, user_id=None)).is_favorited is False


async def test_kb_favorite_of_other_entity_type_with_same_id_does_not_leak(db_session, make_user):
    user = await make_user()
    article = await _kb_article(db_session, 5)
    # то же числовое id, но это избранный FAQ-вопрос, а не статья
    await FavoriteRepository(db_session).add(user.id, "faq_entry", article.id)

    detail = await KbArticleService(db_session).get_detail(article.id, user_id=user.id)

    assert detail.is_favorited is False


# --- FAQ ---

async def test_faq_list_marks_favorites(db_session, make_user):
    user = await make_user()
    liked = await _faq_entry(db_session, 1)
    plain = await _faq_entry(db_session, 2)
    await FavoriteRepository(db_session).add(user.id, "faq_entry", liked.id)

    page = await FaqEntryService(db_session).list_entries(None, None, user_id=user.id)

    flags = {item.id: item.is_favorited for item in page.items}
    assert flags[liked.id] is True
    assert flags[plain.id] is False


async def test_faq_list_for_guest_is_never_favorited(db_session, make_user):
    user = await make_user()
    entry = await _faq_entry(db_session, 3)
    await FavoriteRepository(db_session).add(user.id, "faq_entry", entry.id)

    page = await FaqEntryService(db_session).list_entries(None, None, user_id=None)

    assert all(item.is_favorited is False for item in page.items)


# --- документы ---

async def test_project_documents_mark_favorites(db_session, make_user, make_project, make_document, link_user_project):
    user = await make_user()
    project = await make_project()
    await link_user_project(user, project)
    liked = await make_document(project_id=project.id)
    plain = await make_document(project_id=project.id)
    await FavoriteRepository(db_session).add(user.id, "document", liked.id)

    page = await UserDocumentService(db_session).list_project_documents(user_id=user.id, project_id=project.id)

    flags = {item.id: item.is_favorited for item in page.items}
    assert flags[liked.id] is True
    assert flags[plain.id] is False
