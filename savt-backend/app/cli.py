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
    from app.services import bitrix_service
    from app.services.bitrix_webhook_service import upsert_project_from_deal

    created = updated = skipped = 0
    start: int | None = 0
    async with AsyncSessionLocal() as session:
        while start is not None:
            page, start = await bitrix_service.list_deal_titles(start)
            for _deal_id, title in page.items():
                project, was_created = await upsert_project_from_deal(session, title)
                if project is None:
                    skipped += 1
                elif was_created:
                    created += 1
                else:
                    updated += 1

    print(f"Готово: создано {created}, обновлено {updated}, пропущено (номер не найден в названии) {skipped}")


def main():
    usage = (
        "Использование:\n"
        "  python -m app.cli create-superadmin <login> <password> [full_name]\n"
        "  python -m app.cli create-admin <login> <password> [full_name]\n"
        "  python -m app.cli create-operator <login> <password> [full_name]\n"
        "  python -m app.cli import-bitrix-deals"
    )

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    command = sys.argv[1]

    if command == "import-bitrix-deals":
        asyncio.run(_import_bitrix_deals())
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
