"""Поиск в остальных списках (не рекламации): ШУ, проекты, пользователи, заявки
(сервисные, регистрация, документы, ШУ по фото, смена номера, сброс пароля),
база знаний, FAQ, журнал аудита, чаты. Общие правила fuzzy_condition:
номер (запрос с цифрами) — только точное вхождение, слова — с опечатками.
Bitrix не вызывается.
"""
from app.models.audit_log import AuditLog
from app.models.faq_category import FaqCategory
from app.models.faq_entry import FaqEntry
from app.models.kbarticle import KbArticle
from app.models.kbcategory import KbCategory
from app.models.message import Message
from app.models.project_contact import ProjectContact
from app.models.registration_request import RegistrationRequest
from app.models.service_request import ServiceRequest
from app.repositories.audit import AuditRepository
from app.repositories.cabinet import CabinetRepository
from app.repositories.chat import ChatRepository, MessageRepository
from app.repositories.faq import FaqCategoryRepository, FaqEntryRepository
from app.repositories.kb import KbArticleRepository, KbCategoryRepository
from app.repositories.project import ProjectRepository
from app.repositories.registration_request import RegistrationRequestRepository
from app.repositories.service_request import ServiceRequestRepository
from app.repositories.user import UserRepository


# --- ШУ ---

async def test_cabinet_search_number_is_exact_not_neighbour(db_session, make_cabinet):
    await make_cabinet(object_number="26_205_1")
    hit = await make_cabinet(object_number="26_204_1")

    items, total = await CabinetRepository(db_session).search(query="26_204_1")

    assert [c.id for c in items] == [hit.id]
    assert total == 1


async def test_cabinet_search_number_by_part(db_session, make_cabinet):
    hit = await make_cabinet(object_number="26_205_1")
    await make_cabinet(object_number="29_001")

    items, _ = await CabinetRepository(db_session).search(query="26_205")

    assert [c.id for c in items] == [hit.id]


async def test_cabinet_search_word_tolerates_typo(db_session, make_cabinet):
    hit = await make_cabinet(admin_internal_name="Вентилятор котельной")
    await make_cabinet(admin_internal_name="Насос подпитки")

    items, _ = await CabinetRepository(db_session).search(query="вентелятор")

    assert [c.id for c in items] == [hit.id]


async def test_cabinet_search_skips_deleted(db_session, make_cabinet):
    from datetime import datetime, timezone
    await make_cabinet(object_number="26_777_1", deleted_at=datetime.now(timezone.utc))

    items, total = await CabinetRepository(db_session).search(query="26_777_1")

    assert items == []
    assert total == 0


# --- проекты ---

async def test_project_search_by_name_and_production_number(db_session, make_project):
    by_name = await make_project(name="Агрокомбинат Ждановичи")
    by_number = await make_project(name="Другой", production_number="26-0412")
    await make_project(name="Посторонний")

    repo = ProjectRepository(db_session)
    assert [p.id for p in (await repo.search(query="Ждановичи"))[0]] == [by_name.id]
    assert [p.id for p in (await repo.search(query="26-0412"))[0]] == [by_number.id]


async def test_project_search_number_is_exact_not_neighbour(db_session, make_project):
    await make_project(name="А", production_number="26-0413")
    hit = await make_project(name="Б", production_number="26-0412")

    items, _ = await ProjectRepository(db_session).search(query="26-0412")

    assert [p.id for p in items] == [hit.id]


async def test_project_search_by_contact_person_and_phone(db_session, make_project):
    project = await make_project(name="Проект с контактом")
    await make_project(name="Без контакта")
    db_session.add(ProjectContact(
        project_id=project.id, bitrix_contact_id="1",
        full_name="Кузнецов Алексей", phones=["+375291112233"], emails=["kuz@example.by"],
    ))
    await db_session.flush()

    repo = ProjectRepository(db_session)
    assert [p.id for p in (await repo.search(query="Кузнецов"))[0]] == [project.id]
    assert [p.id for p in (await repo.search(query="1112233"))[0]] == [project.id]
    assert [p.id for p in (await repo.search(query="kuz@"))[0]] == [project.id]


async def test_project_search_skips_deleted(db_session, make_project):
    from datetime import datetime, timezone
    await make_project(name="Закрытый объект", deleted_at=datetime.now(timezone.utc))

    items, _ = await ProjectRepository(db_session).search(query="Закрытый")

    assert items == []


async def test_project_search_company_filter(db_session, make_project):
    hit = await make_project(name="П1", company_name="ООО Ромашка")
    await make_project(name="П2", company_name="ЗАО Лютик")

    items, _ = await ProjectRepository(db_session).search(company="Ромашка")

    assert [p.id for p in items] == [hit.id]


# --- пользователи (админка) ---

async def test_user_search_by_name_phone_organization(db_session, make_user):
    hit = await make_user(full_name="Сидоров Семён", phone="+375291119999", organization_name="Рога и копыта")
    await make_user(full_name="Иванов Иван", phone="+375290000001")

    repo = UserRepository(db_session)
    assert [u.id for u, _ in (await repo.admin_search(query="Сидоров"))[0]] == [hit.id]
    assert [u.id for u, _ in (await repo.admin_search(query="1119999"))[0]] == [hit.id]
    assert [u.id for u, _ in (await repo.admin_search(query="копыта"))[0]] == [hit.id]


async def test_user_search_phone_is_exact_not_neighbour(db_session, make_user):
    await make_user(phone="+375291110001")
    hit = await make_user(phone="+375291110002")

    items, _ = await UserRepository(db_session).admin_search(query="+375291110002")

    assert [u.id for u, _ in items] == [hit.id]


async def test_user_search_does_not_show_superadmin_or_bot(db_session, make_user):
    await make_user("superadmin", full_name="Секретный Суперадмин")

    items, _ = await UserRepository(db_session).admin_search(query="Секретный")

    assert items == []


# --- заявки на сервис ---

async def _service_request(db_session, user, **kw):
    sr = ServiceRequest(
        user_id=user.id, request_type=kw.pop("request_type", "repair"),
        is_under_warranty=False, description=kw.pop("description", "Не включается"), **kw,
    )
    db_session.add(sr)
    await db_session.flush()
    return sr


async def test_service_request_search_by_description_and_applicant(db_session, make_user, make_cabinet):
    applicant = await make_user(full_name="Петров Пётр", phone="+375291234567")
    cabinet = await make_cabinet(object_number="26_204_1")
    hit = await _service_request(db_session, applicant, cabinet_id=cabinet.id, description="Греется контактор")
    other = await make_user(full_name="Иванов Иван", phone="+375290000009")
    await _service_request(db_session, other, cabinet_id=cabinet.id, description="Сбой панели")

    repo = ServiceRequestRepository(db_session)
    assert [r[0].id for r in (await repo.list_admin(search="контактор"))[0]] == [hit.id]
    assert [r[0].id for r in (await repo.list_admin(search="Петров"))[0]] == [hit.id]
    assert [r[0].id for r in (await repo.list_admin(search="1234567"))[0]] == [hit.id]


async def test_service_request_search_cabinet_number_is_exact(db_session, make_user, make_cabinet):
    user = await make_user(full_name="Иванов Иван")
    near = await make_cabinet(object_number="26_205_1")
    target = await make_cabinet(object_number="26_204_1")
    await _service_request(db_session, user, cabinet_id=near.id)
    hit = await _service_request(db_session, user, cabinet_id=target.id)

    items, _ = await ServiceRequestRepository(db_session).list_admin(search="26_204_1")

    assert [r[0].id for r in items] == [hit.id]


async def test_service_request_search_by_project_name(db_session, make_user, make_project):
    user = await make_user(full_name="Иванов Иван")
    project = await make_project(name="Молокозавод Берёзка")
    hit = await _service_request(db_session, user, project_id=project.id)

    items, _ = await ServiceRequestRepository(db_session).list_admin(search="Берёзка")

    assert [r[0].id for r in items] == [hit.id]


async def test_service_request_search_combines_with_status(db_session, make_user, make_project):
    user = await make_user(full_name="Иванов Иван")
    project_id = (await make_project()).id
    open_hit = await _service_request(db_session, user, project_id=project_id, description="Течёт насос", status="open")
    await _service_request(db_session, user, project_id=project_id, description="Течёт насос", status="closed")

    items, total = await ServiceRequestRepository(db_session).list_admin(search="насос", status="open")

    assert [r[0].id for r in items] == [open_hit.id]
    assert total == 1


# --- заявки на регистрацию ---

async def test_registration_request_search(db_session):
    hit = RegistrationRequest(
        phone="+375291110002", hashed_password="x", full_name="Новиков Николай",
        user_type="company", organization_name="ООО Звезда",
    )
    near = RegistrationRequest(phone="+375291110003", hashed_password="x", full_name="Другой", user_type="company")
    db_session.add_all([hit, near])
    await db_session.flush()

    repo = RegistrationRequestRepository(db_session)
    assert [r.id for r in (await repo.list_requests(search="Новиков"))[0]] == [hit.id]
    assert [r.id for r in (await repo.list_requests(search="Звезда"))[0]] == [hit.id]
    # соседний номер не подмешивается
    assert [r.id for r in (await repo.list_requests(search="+375291110002"))[0]] == [hit.id]


# --- база знаний ---

async def _kb_category(db_session, name, slug, **kw):
    cat = KbCategory(name=name, slug=slug, **kw)
    db_session.add(cat)
    await db_session.flush()
    return cat


async def test_kb_article_search_by_title_and_content(db_session):
    cat = await _kb_category(db_session, "Общее", "obschee")
    by_title = KbArticle(category_id=cat.id, title="Настройка частотника", slug="a1", content="текст")
    by_content = KbArticle(category_id=cat.id, title="Другая статья", slug="a2", content="Сбросьте ошибку преобразователя")
    other = KbArticle(category_id=cat.id, title="Посторонняя", slug="a3", content="ничего")
    db_session.add_all([by_title, by_content, other])
    await db_session.flush()

    repo = KbArticleRepository(db_session)
    assert [a.id for a in (await repo.list_articles(search="частотника"))[0]] == [by_title.id]
    assert [a.id for a in (await repo.list_articles(search="преобразователя"))[0]] == [by_content.id]


async def test_kb_article_search_tolerates_typo(db_session):
    cat = await _kb_category(db_session, "Общее", "obschee")
    hit = KbArticle(category_id=cat.id, title="Подключение вентилятора", slug="b1")
    db_session.add(hit)
    await db_session.flush()

    items, _ = await KbArticleRepository(db_session).list_articles(search="вентелятора")

    assert [a.id for a in items] == [hit.id]


async def test_kb_article_search_respects_published_filter(db_session):
    cat = await _kb_category(db_session, "Общее", "obschee")
    published = KbArticle(category_id=cat.id, title="Схема питания", slug="c1", is_published=True)
    draft = KbArticle(category_id=cat.id, title="Схема питания черновик", slug="c2", is_published=False)
    db_session.add_all([published, draft])
    await db_session.flush()

    items, total = await KbArticleRepository(db_session).list_articles(search="питания", is_published=True)

    assert [a.id for a in items] == [published.id]
    assert total == 1


async def test_kb_category_search_by_name_and_description(db_session):
    by_name = await _kb_category(db_session, "Частотные преобразователи", "k1")
    by_desc = await _kb_category(db_session, "Прочее", "k2", description="Всё про насосы")
    await _kb_category(db_session, "Мусор", "k3")

    repo = KbCategoryRepository(db_session)
    assert [c.id for c in await repo.list_all(search="преобразователи")] == [by_name.id]
    assert [c.id for c in await repo.list_all(search="насосы")] == [by_desc.id]


# --- FAQ ---

async def test_faq_entry_search_by_question_and_answer(db_session):
    cat = FaqCategory(name="Вопросы")
    db_session.add(cat)
    await db_session.flush()
    by_question = FaqEntry(category_id=cat.id, question="Как сбросить ошибку?", answer="Нажмите reset")
    by_answer = FaqEntry(category_id=cat.id, question="Что делать?", answer="Перезапустите контроллер")
    other = FaqEntry(category_id=cat.id, question="Прочее", answer="Ничего")
    db_session.add_all([by_question, by_answer, other])
    await db_session.flush()

    repo = FaqEntryRepository(db_session)
    assert [e.id for e in (await repo.list_entries(search="сбросить"))[0]] == [by_question.id]
    assert [e.id for e in (await repo.list_entries(search="контроллер"))[0]] == [by_answer.id]


async def test_faq_category_search(db_session):
    hit = FaqCategory(name="Гарантия и сервис")
    other = FaqCategory(name="Монтаж")
    db_session.add_all([hit, other])
    await db_session.flush()

    items = await FaqCategoryRepository(db_session).list_all(search="гарантия")

    assert [c.id for c in items] == [hit.id]


# --- журнал аудита ---

async def _audit(db_session, **kw):
    row = AuditLog(**kw)
    db_session.add(row)
    await db_session.flush()
    return row


async def test_audit_search_by_action_entity_and_payload(db_session):
    by_action = await _audit(db_session, action="project.delete", entity_type="project", payload={})
    by_payload = await _audit(db_session, action="user.update", entity_type="user", payload={"note": "смена телефона"})
    await _audit(db_session, action="chat.close", entity_type="chat", payload={})

    repo = AuditRepository(db_session)
    assert [r.id for r, _ in (await repo.list_logs(search="project.delete", search_in="action"))[0]] == [by_action.id]
    assert [r.id for r, _ in (await repo.list_logs(search="телефона", search_in="payload"))[0]] == [by_payload.id]
    assert [r.id for r, _ in (await repo.list_logs(search="телефона", search_in="all"))[0]] == [by_payload.id]


async def test_audit_search_by_actor_name(db_session, make_user):
    actor = await make_user("admin", full_name="Администратор Анна")
    hit = await _audit(db_session, actor_id=actor.id, action="x.y", entity_type="x", payload={})
    await _audit(db_session, action="x.z", entity_type="x", payload={})

    items, _ = await AuditRepository(db_session).list_logs(search="Анна", search_in="actor_name")

    assert [r.id for r, _ in items] == [hit.id]


async def test_audit_search_number_in_payload_is_exact(db_session):
    await _audit(db_session, action="a.b", entity_type="x", payload={"cabinet": "26_205_1"})
    hit = await _audit(db_session, action="a.c", entity_type="x", payload={"cabinet": "26_204_1"})

    items, _ = await AuditRepository(db_session).list_logs(search="26_204_1", search_in="payload")

    assert [r.id for r, _ in items] == [hit.id]


# --- чаты (ilike: подстрока без нечёткости) ---

async def test_operator_chat_list_search(db_session, make_user, make_cabinet, make_chat):
    owner = await make_user(full_name="Сидоров Семён", phone="+375291119999")
    other = await make_user(full_name="Иванов Иван", phone="+375290000001")
    cabinet = await make_cabinet(object_number="26_204_1")
    hit = await make_chat(owner, cabinet_id=cabinet.id)
    await make_chat(other, chat_type="support")
    operator = await make_user("operator", full_name="Оператор Олег")

    repo = ChatRepository(db_session)
    for term in ("Сидоров", "1119999", "26_204_1"):
        rows = await repo.list_for_operator(operator.id, search=term)
        assert [r[0].id for r in rows] == [hit.id], term


async def test_operator_chat_list_search_underscore_is_not_a_wildcard(db_session, make_user, make_cabinet, make_chat):
    owner = await make_user(full_name="Иванов Иван")
    await make_chat(owner, cabinet_id=(await make_cabinet(object_number="26X204X1")).id)
    operator = await make_user("operator", full_name="Оператор Олег")

    rows = await ChatRepository(db_session).list_for_operator(operator.id, search="26_204_1")

    assert rows == []


async def test_chat_message_search_in_one_chat(db_session, make_user, make_chat):
    owner = await make_user(full_name="Иванов Иван")
    chat = await make_chat(owner, chat_type="support")
    hit = Message(chat_id=chat.id, sender_id=owner.id, text="Привет, не работает насос")
    miss = Message(chat_id=chat.id, sender_id=owner.id, text="Спасибо")
    db_session.add_all([hit, miss])
    await db_session.flush()

    rows = await MessageRepository(db_session).get_messages(chat.id, search="НАСОС")

    assert [m.id for m, _ in rows] == [hit.id]


async def test_chat_message_search_percent_is_not_a_wildcard(db_session, make_user, make_chat):
    owner = await make_user(full_name="Иванов Иван")
    chat = await make_chat(owner, chat_type="support")
    db_session.add(Message(chat_id=chat.id, sender_id=owner.id, text="обычное сообщение"))
    await db_session.flush()

    rows = await MessageRepository(db_session).get_messages(chat.id, search="%")

    assert rows == []


async def test_global_message_search_skips_deleted(db_session, make_user, make_chat):
    from datetime import datetime, timezone
    owner = await make_user(full_name="Иванов Иван")
    chat = await make_chat(owner, chat_type="support")
    live = Message(chat_id=chat.id, sender_id=owner.id, text="Течёт гидроаккумулятор")
    gone = Message(chat_id=chat.id, sender_id=owner.id, text="Течёт гидроаккумулятор",
                   deleted_at=datetime.now(timezone.utc))
    db_session.add_all([live, gone])
    await db_session.flush()

    items, total = await MessageRepository(db_session).search_global("гидроаккумулятор")

    assert [m.id for m, _, _ in items] == [live.id]
    assert total == 1


async def test_cabinet_search_words_match_in_different_columns(db_session, make_cabinet):
    hit = await make_cabinet(type="ШУ-18К", object_number="26_204_1")
    await make_cabinet(type="ШУ-18К", object_number="29_001")

    items, _ = await CabinetRepository(db_session).search(query="ШУ 26")

    assert [c.id for c in items] == [hit.id]


async def test_user_search_full_name_words_in_any_order(db_session, make_user):
    hit = await make_user(full_name="Сидоров Семён Петрович")
    await make_user(full_name="Сидоров Иван")

    items, _ = await UserRepository(db_session).admin_search(query="Семён Сидоров")

    assert [u.id for u, _ in items] == [hit.id]
