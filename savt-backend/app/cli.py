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


def main():
    usage = (
        "Использование:\n"
        "  python -m app.cli create-admin <login> <password> [full_name]\n"
        "  python -m app.cli create-operator <login> <password> [full_name]"
    )

    if len(sys.argv) < 4 or sys.argv[1] not in ("create-admin", "create-operator"):
        print(usage)
        sys.exit(1)

    command = sys.argv[1]
    login = sys.argv[2]
    password = sys.argv[3]
    full_name = sys.argv[4] if len(sys.argv) > 4 else None

    role_name = RoleName.ADMIN.value if command == "create-admin" else RoleName.OPERATOR.value
    asyncio.run(_create_staff(login, password, full_name, role_name))


if __name__ == "__main__":
    main()
