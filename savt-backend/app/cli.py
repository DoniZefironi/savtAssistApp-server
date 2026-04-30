import asyncio
import sys

from app.core.constants import RoleName
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.models.roles import Role
from app.models.user import User
from app.repositories.user import UserRepository
from sqlalchemy import select


async def create_admin(phone: str, password: str, full_name: str | None = None) -> None:
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        existing = await user_repo.find_by_phone(phone)
        if existing is not None:
            print(f"Пользователь с телефоном {phone} уже существует")
            return

        result = await session.execute(select(Role).where(Role.name == RoleName.ADMIN.value))
        admin_role = result.scalar_one_or_none()
        if admin_role is None:
            print("Роль 'admin' не найдена в БД. Применили ли миграции?")
            return

        await user_repo.create(
            phone=phone,
            full_name=full_name,
            hashed_password=hash_password(password),
            role_id=admin_role.id,
            is_phone_verified=True,
            is_active=True,
        )
        await session.commit()
        print(f"Админ создан: {phone}")


def main():
    if len(sys.argv) < 4 or sys.argv[1] != "create-admin":
        print("Использование: python -m app.cli create-admin <phone> <password> [full_name]")
        sys.exit(1)

    phone = sys.argv[2]
    password = sys.argv[3]
    full_name = sys.argv[4] if len(sys.argv) > 4 else None

    asyncio.run(create_admin(phone, password, full_name))


if __name__ == "__main__":
    main()