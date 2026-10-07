"""Сотрудники Bitrix -> операторы/админы/суперадмины системы.

Источник правды — Bitrix: роль следует отделам сотрудника (см.
app/core/staff_departments.py), уволенный/неактивный/ушедший в отдел без роли
деактивируется (не удаляется — на него ссылается история чатов и заявок).
Вход — по номеру телефона (он лежит в User.login, а User.phone остаётся
пустым: телефон здесь не подтверждён Telegram и не должен быть логином
мобильного приложения). Новым сотрудникам ставится общий начальный пароль из
BITRIX_STAFF_INITIAL_PASSWORD, сменить его обязательно при первом входе.

Меняются только учётки, которые синхронизация сама завела или привязала по
номеру (User.bitrix_user_id). Сотрудник, чей номер занят обычным пользователем
мобильного приложения, не заводится — в отчёте конфликт."""
import logging
from dataclasses import dataclass, field, fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RoleName
from app.core.security import hash_password
from app.core.staff_departments import EXCLUDED_BITRIX_USER_IDS, role_for_departments, role_rank
from app.models.role import Role
from app.models.user import User
from app.repositories.auth import RefreshTokenRepository
from app.services.audit_service import AuditLogger
from app.utils.phone import INVALID_PHONE, normalize_loose_phone

_log = logging.getLogger(__name__)

STAFF_ROLES = (RoleName.OPERATOR.value, RoleName.ADMIN.value, RoleName.SUPERADMIN.value)

@dataclass
class StaffSyncReport:
    created: list[dict] = field(default_factory=list)
    linked: list[dict] = field(default_factory=list)
    role_changed: list[dict] = field(default_factory=list)
    reactivated: list[dict] = field(default_factory=list)
    deactivated: list[dict] = field(default_factory=list)
    skipped_no_phone: list[dict] = field(default_factory=list)
    skipped_invalid_phone: list[dict] = field(default_factory=list)
    skipped_duplicate_phone: list[dict] = field(default_factory=list)
    skipped_conflict: list[dict] = field(default_factory=list)
    skipped_no_password: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def counts(self) -> dict:
        return {f.name: len(getattr(self, f.name)) for f in fields(self)}


def _full_name(u: dict) -> str:
    name = " ".join(p for p in (u.get("LAST_NAME"), u.get("NAME"), u.get("SECOND_NAME")) if p)
    return name or f"Сотрудник Bitrix {u.get('ID')}"


def _is_active(u: dict) -> bool:
    return u.get("ACTIVE") in (True, "Y", "true", "1", 1)


def _departments(u: dict) -> list[int]:
    return [int(d) for d in (u.get("UF_DEPARTMENT") or []) if str(d).isdigit()]


def _row(u: dict, **extra) -> dict:
    return {"bitrix_user_id": int(u["ID"]), "full_name": _full_name(u), **extra}


async def sync_staff(
    session: AsyncSession, bitrix_users: list[dict], initial_password: str,
    actor_id: int | None = None, actor_role: str | None = None,
) -> StaffSyncReport:
    report = StaffSyncReport()
    roles = {r.name: r for r in (await session.execute(select(Role))).scalars().all()}
    role_names = {r.id: r.name for r in roles.values()}
    tokens = RefreshTokenRepository(session)

    linked = {
        u.bitrix_user_id: u
        for u in (await session.execute(select(User).where(User.bitrix_user_id.is_not(None)))).scalars().all()
    }

    wanted: list[tuple[dict, RoleName, str | None | bool]] = []
    seen_ids: set[int] = set()
    for u in bitrix_users:
        bitrix_id = int(u["ID"])
        seen_ids.add(bitrix_id)
        role = None
        if _is_active(u) and u.get("USER_TYPE") == "employee" and bitrix_id not in EXCLUDED_BITRIX_USER_IDS:
            role = role_for_departments(_departments(u))
        if role is None:
            continue
        wanted.append((u, role, normalize_loose_phone(u.get("WORK_PHONE") or u.get("PERSONAL_MOBILE"))))
    wanted_ids = {int(u["ID"]) for u, _, _ in wanted}

    phone_counts: dict[str, int] = {}
    for _, _, phone in wanted:
        if isinstance(phone, str):
            phone_counts[phone] = phone_counts.get(phone, 0) + 1

    # Деактивация: учётку завела синхронизация, а в Bitrix сотрудник теперь
    # уволен, ушёл в отдел без роли, стал внешним или исчез совсем
    for bitrix_id, user in linked.items():
        if bitrix_id in wanted_ids or not user.is_active:
            continue
        user.is_active = False
        await tokens.revoke_all_for_user(user.id)
        report.deactivated.append({
            "bitrix_user_id": bitrix_id, "full_name": user.full_name,
            "reason": "нет в Bitrix" if bitrix_id not in seen_ids else "уволен или нет роли по отделу",
        })

    for u, role, phone in wanted:
        bitrix_id = int(u["ID"])
        row = _row(u, role=role.value)
        user = linked.get(bitrix_id)

        if user is not None:
            if not user.is_active:
                user.is_active = True
                report.reactivated.append(row)
            if role_names.get(user.role_id) != role.value:
                report.role_changed.append({**row, "from": role_names.get(user.role_id)})
                user.role_id = roles[role.value].id
            user.full_name = _full_name(u)
            # Сменили номер в Bitrix — меняется и логин, но только у учёток,
            # которые завела синхронизация (телефон пустой), и если новый
            # логин свободен
            if (
                isinstance(phone, str) and phone_counts[phone] == 1
                and user.phone is None and user.login != phone
                and await _find_by_phone_or_login(session, phone) is None
            ):
                user.login = phone
            continue

        if phone is None:
            report.skipped_no_phone.append(row)
            continue
        if phone is INVALID_PHONE:
            report.skipped_invalid_phone.append({**row, "raw": u.get("WORK_PHONE") or u.get("PERSONAL_MOBILE")})
            continue
        if phone_counts[phone] > 1:
            report.skipped_duplicate_phone.append({**row, "phone": phone})
            continue

        existing = await _find_by_phone_or_login(session, phone)
        if existing is not None:
            existing_role = role_names.get(existing.role_id)
            if existing_role not in STAFF_ROLES:
                report.skipped_conflict.append({**row, "phone": phone, "reason": "номер занят пользователем мобильного приложения"})
                continue
            if existing.bitrix_user_id is not None:
                report.skipped_conflict.append({**row, "phone": phone, "reason": "номер уже привязан к другому сотруднику"})
                continue
            existing.bitrix_user_id = bitrix_id
            if existing.login is None:
                existing.login = phone
            # Роль повышается, но не понижается: у вручную заведённого
            # суперадмина роль выше, чем даёт его отдел
            if role_rank(role.value) > role_rank(existing_role):
                existing.role_id = roles[role.value].id
                report.role_changed.append({**row, "from": existing_role})
            report.linked.append(row)
            continue

        if not initial_password:
            report.skipped_no_password.append({**row, "phone": phone})
            continue
        session.add(User(
            login=phone, phone=None, full_name=_full_name(u),
            email=await _free_email(session, u.get("EMAIL")),
            hashed_password=hash_password(initial_password),
            role_id=roles[role.value].id,
            is_phone_verified=True, is_verified=True, is_active=True,
            bitrix_user_id=bitrix_id, must_change_password=True,
        ))
        await session.flush()
        report.created.append({**row, "login": phone})

    AuditLogger(session).log("staff.bitrix_sync", "user", None, actor_id, actor_role, report.counts())
    await session.commit()
    return report


async def _find_by_phone_or_login(session: AsyncSession, phone: str) -> User | None:
    return (await session.execute(
        select(User).where((User.phone == phone) | (User.login == phone))
    )).scalars().first()


async def _free_email(session: AsyncSession, email: str | None) -> str | None:
    if not email:
        return None
    taken = (await session.execute(select(User.id).where(User.email == email))).first()
    return None if taken else email


async def run_sync(
    session: AsyncSession, actor_id: int | None = None, actor_role: str | None = None,
) -> StaffSyncReport | None:
    """Забирает сотрудников из Bitrix и синхронизирует. None — Bitrix не
    настроен или не ответил: ничего не меняется."""
    from app.config import settings
    from app.services import bitrix_service

    users = await bitrix_service.list_users_for_sync()
    if users is None:
        return None
    return await sync_staff(session, users, settings.bitrix_staff_initial_password, actor_id, actor_role)


async def sync_staff_from_bitrix_job() -> None:
    """Фоновая задача по расписанию (см. main.py)."""
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            report = await run_sync(session)
        except Exception:
            _log.exception("Синхронизация сотрудников из Bitrix упала")
            return
    if report is None:
        _log.warning("Синхронизация сотрудников из Bitrix пропущена: Bitrix не ответил")
    else:
        _log.info("Синхронизация сотрудников из Bitrix: %s", report.counts())
