"""ProjectService, CabinetService и UserProjectService: карточки и списки, правка,
каскадное удаление с архивацией чатов, привязка ШУ к проекту, участники проекта,
вход в проект по QR со слиянием прямых ШУ, закрепление и выход. Внешнее
(папки на NAS, realtime, push, SIM-сервис) подменяется и записывается."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.exceptions import AlreadyExistsError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.cabinets import Cabinet
from app.models.notification import Notification
from app.models.tag import Tag
from app.models.warranty_notif_log import WarrantyNotifLog
from app.schemas.cabinet import CabinetCreateIn, CabinetUpdateIn
from app.schemas.project import CabinetProjectPatchIn, ProjectUpdateIn
from app.services import notification_service, project_folder_service, realtime_events
from app.services.cabinet_service import CabinetService
from app.services.project_service import ProjectService
from app.services.user_project_service import UserProjectService


ADMIN = SimpleNamespace(id=None)


@pytest.fixture(autouse=True)
async def _admin(make_user):
    """Действующий администратор: журнал аудита ссылается на реального пользователя."""
    ADMIN.id = (await make_user("admin")).id


@pytest.fixture
def env(monkeypatch):
    e = SimpleNamespace(folders=[], chat_updates=[], chat_created=[], cabinet_created=[], pushes=[])

    async def push(session, user_id, title, body, data=None, notification_type=None):
        e.pushes.append((user_id, title))

    async def chat_updated(chat_id, summary):
        e.chat_updates.append(chat_id)

    async def chat_created(chat_id, summary):
        e.chat_created.append(chat_id)

    async def cabinet_created(cabinet_id, project_id, user_ids):
        e.cabinet_created.append((cabinet_id, project_id, sorted(user_ids)))

    monkeypatch.setattr(project_folder_service, "schedule_cabinet_folder", e.folders.append)
    monkeypatch.setattr(notification_service, "send_push", push)
    monkeypatch.setattr(realtime_events, "publish_chat_updated", chat_updated)
    monkeypatch.setattr(realtime_events, "publish_chat_created", chat_created)
    monkeypatch.setattr(realtime_events, "publish_cabinet_created", cabinet_created)
    return e


async def _audit(db_session, action):
    return list((await db_session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _notifications(db_session, user):
    return list((await db_session.execute(select(Notification).where(Notification.user_id == user.id))).scalars())


# --- проекты: карточка и список ---

async def test_project_card(db_session, env, make_project, make_cabinet):
    project = await make_project(name="Космос", company_name="ООО Ромашка",
                                 warranty_ends_at=datetime.now(timezone.utc) + timedelta(days=100))
    await make_cabinet(project_id=project.id, admin_internal_name="ШУ-1")
    await make_cabinet(project_id=project.id, deleted_at=datetime.now(timezone.utc))

    card = await ProjectService(db_session).get(project.id)

    assert (card.name, card.company_name) == ("Космос", "ООО Ромашка")
    assert [c.admin_internal_name for c in card.cabinets] == ["ШУ-1"]
    assert card.warranty_status == "active"


async def test_deleted_or_unknown_project_is_not_found(db_session, env, make_project):
    deleted = await make_project(deleted_at=datetime.now(timezone.utc))
    svc = ProjectService(db_session)

    for call in (
        lambda: svc.get(deleted.id),
        lambda: svc.get(999999),
        lambda: svc.update(deleted.id, ProjectUpdateIn(), ADMIN.id, "admin"),
        lambda: svc.delete(deleted.id, ADMIN.id, "admin"),
        lambda: svc.sync_folder_now(deleted.id),
        lambda: svc.list_project_users(deleted.id),
    ):
        with pytest.raises(NotFoundError):
            await call()


async def test_project_list_counts_cabinets_and_year(db_session, env, make_project, make_cabinet):
    project = await make_project(name="Список-1", production_number="26_100")
    await make_cabinet(project_id=project.id)
    await make_cabinet(project_id=project.id)
    await make_project(name="Список-2", production_number="25_050")

    page = await ProjectService(db_session).list_all(query="Список-1")
    from_date = await ProjectService(db_session).list_all(
        query="Список", shipment_planned_from=datetime.now().date(), shipment_planned_to=datetime.now().date(),
    )

    [item] = page.items
    assert item.cabinet_count == 2 and item.id == project.id and item.year == 2026
    assert from_date.items == []  # у проектов нет даты отгрузки — диапазон их исключает


async def test_day_bounds_cover_the_whole_last_day():
    from datetime import date
    from app.services.project_service import _day_start, _next_day_start

    assert _day_start(None) is None and _next_day_start(None) is None
    assert _day_start(date(2026, 8, 15)) == datetime(2026, 8, 15, tzinfo=timezone.utc)
    assert _next_day_start(date(2026, 8, 15)) == datetime(2026, 8, 16, tzinfo=timezone.utc)


# --- проекты: правка ---

async def test_update_project_warranty_and_parent(db_session, env, make_project):
    parent, child = await make_project(), await make_project()
    ends = datetime.now(timezone.utc) + timedelta(days=30)

    out = await ProjectService(db_session).update(
        child.id, ProjectUpdateIn(parent_project_id=parent.id, warranty_ends_at=ends), ADMIN.id, "admin",
    )

    assert out.parent_project_id == parent.id and out.warranty_ends_at == ends
    [entry] = await _audit(db_session, "project.update")
    assert set(entry.payload["fields"]) == {"parent_project_id", "warranty_ends_at"}


async def test_update_project_parent_rules(db_session, env, make_project):
    a = await make_project()
    b = await make_project(parent_project_id=a.id)
    gone = await make_project(deleted_at=datetime.now(timezone.utc))
    svc = ProjectService(db_session)

    with pytest.raises(AlreadyExistsError):
        await svc.update(a.id, ProjectUpdateIn(parent_project_id=a.id), ADMIN.id, "admin")   # сам в себя
    with pytest.raises(AlreadyExistsError):
        await svc.update(a.id, ProjectUpdateIn(parent_project_id=b.id), ADMIN.id, "admin")   # цикл
    with pytest.raises(NotFoundError):
        await svc.update(a.id, ProjectUpdateIn(parent_project_id=gone.id), ADMIN.id, "admin")
    with pytest.raises(NotFoundError):
        await svc.update(a.id, ProjectUpdateIn(parent_project_id=999999), ADMIN.id, "admin")


async def test_update_project_can_detach_parent(db_session, env, make_project):
    parent = await make_project()
    child = await make_project(parent_project_id=parent.id)

    out = await ProjectService(db_session).update(child.id, ProjectUpdateIn(parent_project_id=None), ADMIN.id, "admin")

    assert out.parent_project_id is None


# --- проекты: удаление ---

async def test_project_delete_cascades_to_cabinets_and_chats(
    db_session, env, make_user, make_project, make_cabinet, make_chat,
):
    user = await make_user()
    project = await make_project(name="Под снос")
    cabinet = await make_cabinet(project_id=project.id)
    cabinet_chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)
    project_chat = await make_chat(user, "project", project_id=project.id)
    support = await make_chat(user, "support")

    await ProjectService(db_session).delete(project.id, ADMIN.id, "admin")

    assert project.deleted_at is not None and cabinet.deleted_at is not None
    assert cabinet_chat.archived_at is not None and project_chat.archived_at is not None
    assert support.archived_at is None
    assert sorted(env.chat_updates) == sorted([cabinet_chat.id, project_chat.id])
    assert len(await _audit(db_session, "project.delete")) == 1 and len(await _audit(db_session, "cabinet.delete")) == 1


# --- проекты: привязка ШУ ---

async def test_attach_and_detach_cabinet(db_session, env, make_project, make_cabinet):
    project, cabinet = await make_project(), await make_cabinet()
    svc = ProjectService(db_session)

    await svc.set_cabinet_project(cabinet.id, CabinetProjectPatchIn(project_id=project.id), ADMIN.id, "admin")
    assert cabinet.project_id == project.id and env.folders == [cabinet.id]

    await svc.set_cabinet_project(cabinet.id, CabinetProjectPatchIn(project_id=None), ADMIN.id, "admin")
    assert cabinet.project_id is None and env.folders == [cabinet.id]  # при отвязке папка не создаётся
    assert len(await _audit(db_session, "cabinet.set_project")) == 2


async def test_attach_cabinet_validation(db_session, env, make_project, make_cabinet):
    gone_project = await make_project(deleted_at=datetime.now(timezone.utc))
    gone_cabinet = await make_cabinet(deleted_at=datetime.now(timezone.utc))
    cabinet = await make_cabinet()
    svc = ProjectService(db_session)

    with pytest.raises(NotFoundError):
        await svc.set_cabinet_project(999999, CabinetProjectPatchIn(project_id=None), ADMIN.id, "admin")
    with pytest.raises(NotFoundError):
        await svc.set_cabinet_project(gone_cabinet.id, CabinetProjectPatchIn(project_id=None), ADMIN.id, "admin")
    with pytest.raises(NotFoundError):
        await svc.set_cabinet_project(cabinet.id, CabinetProjectPatchIn(project_id=gone_project.id), ADMIN.id, "admin")
    assert cabinet.project_id is None and env.folders == []


# --- проекты: участники ---

async def test_project_members_list(db_session, env, make_user, make_project, link_user_project):
    project = await make_project()
    member = await make_user(full_name="Участник")
    await link_user_project(member, project)

    users = await ProjectService(db_session).list_project_users(project.id)

    assert [(u.user_id, u.full_name) for u in users] == [(member.id, "Участник")]


async def test_removing_member_archives_his_chats_and_notifies(
    db_session, env, make_user, make_project, make_cabinet, make_chat, link_user_project,
):
    member, other = await make_user(), await make_user()
    project = await make_project(name="Космос")
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(member, project)
    await link_user_project(other, project)
    mine = await make_chat(member, "cabinet", cabinet_id=cabinet.id)
    theirs = await make_chat(other, "cabinet", cabinet_id=cabinet.id)

    await ProjectService(db_session).remove_user_from_project(project.id, member.id, "Увольнение", ADMIN.id, "admin")

    assert mine.archived_at is not None and theirs.archived_at is None
    assert env.chat_updates == [mine.id]
    [note] = await _notifications(db_session, member)
    assert "Космос" in note.body
    [entry] = await _audit(db_session, "user_project.remove")
    assert entry.payload["reason"] == "Увольнение"
    assert [u.user_id for u in await ProjectService(db_session).list_project_users(project.id)] == [other.id]


async def test_removing_non_member_is_not_found(db_session, env, make_user, make_project):
    with pytest.raises(NotFoundError):
        await ProjectService(db_session).remove_user_from_project(
            (await make_project()).id, (await make_user()).id, "x", ADMIN.id, "admin",
        )


# --- проекты: синхронизация папок ---

async def test_sync_folder_requires_configured_root(db_session, env, make_project, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "project_folders_root", "")
    project = await make_project()

    with pytest.raises(NotFoundError):
        await ProjectService(db_session).sync_folder_now(project.id)
    with pytest.raises(NotFoundError):
        await ProjectService(db_session).sync_all_folders_now()


async def test_sync_folder_reports_imported_documents(db_session, env, make_project, make_cabinet, make_document, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "project_folders_root", "/nas")
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)

    async def fake_sync(session, proj):
        await make_document(project_id=project.id)
        await make_document(cabinet_id=cabinet.id, title="Руководство")
        proj.folder_synced_at = datetime.now(timezone.utc)

    monkeypatch.setattr(project_folder_service, "sync_project_folder", fake_sync)

    result = await ProjectService(db_session).sync_folder_now(project.id)

    assert result["imported_documents"] == 2 and "2" in result["message"]
    assert result["synced_at"] is not None


async def test_sync_folder_without_news(db_session, env, make_project, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "project_folders_root", "/nas")
    project = await make_project()

    async def nothing(session, proj):
        return None

    monkeypatch.setattr(project_folder_service, "sync_project_folder", nothing)

    result = await ProjectService(db_session).sync_folder_now(project.id)

    assert result["imported_documents"] == 0 and "новых файлов не найдено" in result["message"]


async def test_sync_all_folders_message(db_session, env, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "project_folders_root", "/nas")

    async def fake_all(session):
        return {"total": 10, "synced": 6, "relocated": 3, "failed": 1}

    monkeypatch.setattr(project_folder_service, "_sync_all_projects", fake_all)

    result = await ProjectService(db_session).sync_all_folders_now()

    assert (result["total_projects"], result["synced_projects"]) == (10, 6)
    assert (result["relocated_projects"], result["failed_projects"]) == (3, 1)
    assert "перенесено" in result["message"] and "с ошибкой: 1" in result["message"]


# --- ШУ ---

async def test_create_cabinet(db_session, env, make_user, make_project, link_user_project):
    member = await make_user()
    project = await make_project(name="Космос")
    await link_user_project(member, project)

    out = await CabinetService(db_session).create(
        CabinetCreateIn(project_id=project.id, type="  ШУ-18К ", object_number="26_001", admin_internal_name="Главный"),
        ADMIN.id, "admin",
    )

    cabinet = await db_session.get(Cabinet, out.id)
    assert out.type == "шу-18к" and out.project_name == "Космос" and cabinet.unique_code
    tag = (await db_session.execute(select(Tag).where(Tag.name == "шу-18к", Tag.scope == "cabinet_type"))).scalar_one()
    assert tag is not None
    assert env.folders == [out.id]
    assert env.cabinet_created == [(out.id, project.id, [member.id])]
    assert len(await _audit(db_session, "cabinet.create")) == 1


async def test_cabinet_type_tag_is_not_duplicated(db_session, env, make_project):
    project = await make_project()
    svc = CabinetService(db_session)

    for number in ("1", "2"):
        await svc.create(CabinetCreateIn(project_id=project.id, type="ШУ-5", object_number=number), ADMIN.id, "admin")

    tags = (await db_session.execute(select(Tag).where(Tag.name == "шу-5", Tag.scope == "cabinet_type"))).scalars().all()
    assert len(tags) == 1


async def test_cabinet_needs_a_live_project(db_session, env, make_project):
    gone = await make_project(deleted_at=datetime.now(timezone.utc))

    for project_id in (gone.id, 999999):
        with pytest.raises(NotFoundError):
            await CabinetService(db_session).create(
                CabinetCreateIn(project_id=project_id, type="ШУ", object_number="1"), ADMIN.id, "admin",
            )


def test_cabinet_warranty_end_must_follow_start():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        CabinetCreateIn(project_id=1, type="ШУ", object_number="1", warranty_starts_at=now, warranty_ends_at=now)


async def test_cabinet_card_with_sim(db_session, env, make_cabinet, monkeypatch):
    from app.services import sim_service

    async def get_sim(sim_id):
        return {"id": sim_id, "serialNumber": "S-1", "phone": "+375291112233", "ip": "10.0.0.5"}

    monkeypatch.setattr(sim_service, "get_sim", get_sim)
    cabinet = await make_cabinet(sim_id="sim-guid")

    out = await CabinetService(db_session).get(cabinet.id)

    assert out.sim.serial_number == "S-1" and out.sim.ip == "10.0.0.5"


async def test_cabinet_card_survives_sim_service_outage(db_session, env, make_cabinet, monkeypatch):
    from app.services import sim_service

    async def get_sim(sim_id):
        return None

    monkeypatch.setattr(sim_service, "get_sim", get_sim)
    cabinet = await make_cabinet(sim_id="sim-guid")

    out = await CabinetService(db_session).get(cabinet.id)

    assert out.sim is None and out.sim_id == "sim-guid"
    with pytest.raises(NotFoundError):
        await CabinetService(db_session).get(999999)


async def test_cabinet_update(db_session, env, make_cabinet):
    cabinet = await make_cabinet(type="старый")
    svc = CabinetService(db_session)

    out = await svc.update(cabinet.id, CabinetUpdateIn(type="Новый", description="Описание"), ADMIN.id, "admin")
    assert (out.type, out.description) == ("новый", "Описание") and env.folders == []

    await svc.update(cabinet.id, CabinetUpdateIn(object_number="99_999"), ADMIN.id, "admin")
    await svc.update(cabinet.id, CabinetUpdateIn(admin_internal_name="Имя"), ADMIN.id, "admin")
    assert env.folders == [cabinet.id, cabinet.id]  # смена номера/названия переносит папку
    with pytest.raises(NotFoundError):
        await svc.update(999999, CabinetUpdateIn(description="x"), ADMIN.id, "admin")


async def test_mqtt_topic_must_be_unique(db_session, env, make_cabinet):
    first, second = await make_cabinet(mqtt_topic="26_001/1/data"), await make_cabinet()
    svc = CabinetService(db_session)

    with pytest.raises(AlreadyExistsError):
        await svc.update(second.id, CabinetUpdateIn(mqtt_topic="26_001/1/data"), ADMIN.id, "admin")
    # свой же топик повторно сохранить можно
    await svc.update(first.id, CabinetUpdateIn(mqtt_topic="26_001/1/data"), ADMIN.id, "admin")


async def test_changing_warranty_end_resets_notification_log(db_session, env, make_cabinet):
    cabinet = await make_cabinet(warranty_ends_at=datetime.now(timezone.utc) + timedelta(days=10))
    db_session.add(WarrantyNotifLog(cabinet_id=cabinet.id, days_before=10))
    await db_session.flush()
    svc = CabinetService(db_session)

    await svc.update(cabinet.id, CabinetUpdateIn(description="без смены гарантии"), ADMIN.id, "admin")
    assert len((await db_session.execute(select(WarrantyNotifLog))).scalars().all()) == 1

    await svc.update(cabinet.id, CabinetUpdateIn(warranty_ends_at=datetime.now(timezone.utc) + timedelta(days=200)), ADMIN.id, "admin")
    assert (await db_session.execute(
        select(WarrantyNotifLog).where(WarrantyNotifLog.cabinet_id == cabinet.id)
    )).scalars().all() == []


async def test_cabinet_delete_archives_its_chats(db_session, env, make_user, make_cabinet, make_chat):
    user = await make_user()
    cabinet = await make_cabinet()
    chat = await make_chat(user, "cabinet", cabinet_id=cabinet.id)
    other = await make_chat(user, "support")

    await CabinetService(db_session).delete(cabinet.id, ADMIN.id, "admin")

    assert cabinet.deleted_at is not None and chat.archived_at is not None and other.archived_at is None
    assert env.chat_updates == [chat.id]
    with pytest.raises(NotFoundError):
        await CabinetService(db_session).delete(cabinet.id, ADMIN.id, "admin")  # повторно — уже нет


async def test_cabinet_list_with_tags_and_projects(db_session, env, make_project, make_cabinet):
    project = await make_project(name="Космос")
    cabinet = await make_cabinet(project_id=project.id, object_number="LIST_77", purpose="Насосная")
    tag = Tag(name="важный", scope="cabinet")
    db_session.add(tag)
    await db_session.flush()
    svc = CabinetService(db_session)
    await svc.set_tags(cabinet.id, [tag.id], ADMIN.id, "admin")

    page = await svc.list_all(query="LIST_77")
    by_tag = await svc.list_all(tag_ids=[tag.id])
    in_project = await svc.list_all(project_id=project.id)

    [item] = page.items
    assert item.project_name == "Космос" and [t.name for t in item.tags] == ["важный"]
    assert [i.id for i in by_tag.items] == [cabinet.id] == [i.id for i in in_project.items]
    assert len(await _audit(db_session, "cabinet.set_tags")) == 1
    with pytest.raises(NotFoundError):
        await svc.set_tags(999999, [], ADMIN.id, "admin")


async def test_geo_points(db_session, env, make_cabinet):
    placed = await make_cabinet(latitude=53.9, longitude=27.5, warranty_ends_at=datetime.now(timezone.utc) + timedelta(days=90))
    await make_cabinet()

    points = await CabinetService(db_session).get_geo()

    by_id = {p.id: p for p in points}
    assert placed.id in by_id and by_id[placed.id].warranty_status == "active"
    assert by_id[placed.id].latitude == 53.9 and by_id[placed.id].has_open_requests is False


# --- проекты пользователя ---

async def test_user_project_list_and_card(db_session, env, make_user, make_project, make_cabinet, link_user_project):
    user = await make_user()
    project = await make_project(name="Космос", company_name="ООО")
    await make_cabinet(project_id=project.id, admin_internal_name="ШУ-1")
    await link_user_project(user, project)
    svc = UserProjectService(db_session)

    [row] = await svc.list_projects(user.id)
    card = await svc.get_project(user.id, project.id)

    assert (row.project_id, row.cabinet_count, row.is_pinned) == (project.id, 1, False)
    assert card.company_name == "ООО" and [c.admin_internal_name for c in card.cabinets] == ["ШУ-1"]
    assert not hasattr(card, "contacts")  # контакты заказчика клиенту не отдаются


async def test_foreign_project_card_is_not_found(db_session, env, make_user, make_project):
    with pytest.raises(NotFoundError):
        await UserProjectService(db_session).get_project((await make_user()).id, (await make_project()).id)


async def test_pin_and_unpin_project(db_session, env, make_user, make_project, link_user_project):
    user, stranger = await make_user(), await make_user()
    project = await make_project()
    await link_user_project(user, project)
    svc = UserProjectService(db_session)

    await svc.pin_project(user.id, project.id)
    assert (await svc.list_projects(user.id))[0].is_pinned is True
    await svc.unpin_project(user.id, project.id)
    assert (await svc.list_projects(user.id))[0].is_pinned is False
    for call in (svc.pin_project, svc.unpin_project, svc.leave_project):
        with pytest.raises(NotFoundError):
            await call(stranger.id, project.id)


# --- вход в проект по QR ---

async def test_add_project_by_qr_creates_membership_and_chat(db_session, env, make_user, make_project):
    user = await make_user()
    project = await make_project(unique_code="qr-code-1")
    svc = UserProjectService(db_session)

    result = await svc.add_by_qr(user.id, "qr-code-1")

    assert result["status"] == "linked"
    assert [r.project_id for r in await svc.list_projects(user.id)] == [project.id]
    assert len(env.chat_created) == 1
    with pytest.raises(AlreadyExistsError):
        await svc.add_by_qr(user.id, "qr-code-1")
    assert len(env.chat_created) == 1


async def test_qr_of_unknown_or_deleted_project(db_session, env, make_user, make_project):
    user = await make_user()
    await make_project(unique_code="dead", deleted_at=datetime.now(timezone.utc))

    for code in ("dead", "no-such-code"):
        with pytest.raises(NotFoundError):
            await UserProjectService(db_session).add_by_qr(user.id, code)


async def test_qr_merges_directly_added_cabinets(
    db_session, env, make_user, make_project, make_cabinet, link_user_cabinet,
):
    from app.models.user_cabinet import UserCabinet
    user = await make_user()
    project = await make_project(unique_code="merge-code", name="Космос")
    inside = await make_cabinet(project_id=project.id)
    outside = await make_cabinet()
    await link_user_cabinet(user, inside)
    await link_user_cabinet(user, outside)

    await UserProjectService(db_session).add_by_qr(user.id, "merge-code")

    left = (await db_session.execute(select(UserCabinet).where(UserCabinet.user_id == user.id))).scalars().all()
    assert [uc.cabinet_id for uc in left] == [outside.id]  # прямая привязка к шкафу проекта заменена членством
    [note] = await _notifications(db_session, user)
    assert "Космос" in note.body


async def test_leaving_project_archives_only_own_chats(
    db_session, env, make_user, make_project, make_cabinet, make_chat, link_user_project,
):
    user, other = await make_user(), await make_user()
    project = await make_project()
    cabinet = await make_cabinet(project_id=project.id)
    await link_user_project(user, project)
    await link_user_project(other, project)
    mine = await make_chat(user, "cabinet", cabinet_id=cabinet.id)
    mine_project = await make_chat(user, "project", project_id=project.id)
    theirs = await make_chat(other, "cabinet", cabinet_id=cabinet.id)

    await UserProjectService(db_session).leave_project(user.id, project.id)

    assert mine.archived_at is not None and mine_project.archived_at is not None and theirs.archived_at is None
    assert sorted(env.chat_updates) == sorted([mine.id, mine_project.id])
    assert await UserProjectService(db_session).list_projects(user.id) == []
