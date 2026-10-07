import asyncio
import sys

from sqlalchemy import select

from app.core.constants import RoleName
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.models.role import Role
from app.repositories.user import UserRepository


async def _create_staff(login: str, password: str, full_name: str | None, role_name: str) -> None:
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)

        existing = await user_repo.find_by_login(login)
        if existing is not None:
            print(f"Пользователь с логином '{login}' уже существует")
            return

        result = await session.execute(select(Role).where(Role.name == role_name))
        role = result.scalar_one_or_none()
        if role is None:
            print(f"Роль '{role_name}' не найдена в БД. Запусти миграции.")
            return

        await user_repo.create(
            login=login,
            full_name=full_name,
            hashed_password=hash_password(password),
            role_id=role.id,
            is_active=True,
            is_phone_verified=True,
        )
        await session.commit()
        print(f"{role_name.capitalize()} создан: {login}")


async def _import_bitrix_deals() -> None:
    from app.config import settings
    from app.services import bitrix_service
    from app.services.bitrix_webhook_service import upsert_project_from_deal

    created = updated = skipped = 0
    start: int | None = 0
    async with AsyncSessionLocal() as session:
        while start is not None:
            page, start = await bitrix_service.list_deals(start)
            for deal in page:
                project, was_created = await upsert_project_from_deal(session, deal)
                if project is None:
                    skipped += 1
                elif was_created:
                    created += 1
                else:
                    updated += 1

    print(f"Готово: создано {created}, обновлено {updated}, пропущено (номер не определился) {skipped}")
    if not settings.bitrix_production_years:
        print(
            "BITRIX_PRODUCTION_YEARS пуст — импортированы сделки за все годы. "
            "Чтобы взять только нужные, укажите, например, 25,26,27 и запустите заново."
        )


_REPORT_TITLES = {
    "created": "Заведены",
    "linked": "Привязаны к существующим учёткам",
    "role_changed": "Сменилась роль",
    "reactivated": "Снова активны",
    "deactivated": "Деактивированы",
    "skipped_no_phone": "Пропущены: нет телефона",
    "skipped_invalid_phone": "Пропущены: телефон не распознан",
    "skipped_duplicate_phone": "Пропущены: один телефон у нескольких сотрудников",
    "skipped_conflict": "Пропущены: конфликт",
    "skipped_no_password": "Пропущены: не задан BITRIX_STAFF_INITIAL_PASSWORD",
}


async def _sync_bitrix_staff() -> None:
    from app.services import bitrix_staff_sync

    async with AsyncSessionLocal() as session:
        report = await bitrix_staff_sync.run_sync(session)
    if report is None:
        print("Bitrix недоступен или не настроен — ничего не изменено")
        sys.exit(1)
    data = report.as_dict()
    print("Итог:", ", ".join(f"{_REPORT_TITLES[k]} — {len(v)}" for k, v in data.items() if v) or "изменений нет")
    for key, rows in data.items():
        if not rows:
            continue
        print(f"\n{_REPORT_TITLES[key]}:")
        for row in rows:
            extra = " ".join(str(row[k]) for k in ("role", "reason") if row.get(k))
            print(f"  [{row['bitrix_user_id']}] {row['full_name']} {extra}".rstrip())


def main():
    usage = (
        "Использование:\n"
        "  python -m app.cli create-superadmin <login> <password> [full_name]\n"
        "  python -m app.cli create-admin <login> <password> [full_name]\n"
        "  python -m app.cli create-operator <login> <password> [full_name]\n"
        "  python -m app.cli import-bitrix-deals\n"
        "  python -m app.cli sync-bitrix-staff"
    )

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    command = sys.argv[1]

    if command == "import-bitrix-deals":
        asyncio.run(_import_bitrix_deals())
        return

    if command == "sync-bitrix-staff":
        asyncio.run(_sync_bitrix_staff())
        return

    role_map = {
        "create-superadmin": RoleName.SUPERADMIN.value,
        "create-admin": RoleName.ADMIN.value,
        "create-operator": RoleName.OPERATOR.value,
    }
    if command not in role_map or len(sys.argv) < 4:
        print(usage)
        sys.exit(1)

    login = sys.argv[2]
    password = sys.argv[3]
    full_name = sys.argv[4] if len(sys.argv) > 4 else None
    asyncio.run(_create_staff(login, password, full_name, role_map[command]))


if __name__ == "__main__":
    main()
