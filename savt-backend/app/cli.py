import asyncio
import sys

from app.core.constants import RoleName
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.models.role import Role
from app.models.user import User
from app.repositories.user import UserRepository
from sqlalchemy import select

# Создаем админа через терминал
async def create_admin(login: str, password: str, full_name: str | None = None) -> None:
    # Создаем сессию
    async with AsyncSessionLocal() as session:
        # Проверяем, нет ли уже такого пользователя
        user_repo = UserRepository(session)
        existing = await user_repo.find_by_phone(login)
        if existing is not None:
            print(f"Пользователь с телефоном {login} уже существует")
            return

        # Ищем роль админа
        result = await session.execute(select(Role).where(Role.name == RoleName.ADMIN.value))
        admin_role = result.scalar_one_or_none()
        if admin_role is None:
            print("Роль 'admin' не найдена в БД")
            return

        # Создаем пользователя с ролью "админ"
        await user_repo.create(
            login=login,
            full_name=full_name,
            hashed_password=hash_password(password),
            role_id=admin_role.id,
            is_active=True,
        )
        await session.commit()
        print(f"Админ создан: {login}")

# Точка входа и парсинг
def main():
    if len(sys.argv) < 4 or sys.argv[1] != "create-admin":
        print("Копируй и пиши строку справа, только замени <login> на логин, без скобок, с паролем так же, " \
        "и имя без этих квадратов, а с норм кавычками по бочкам: python -m app.cli create-admin <login> <password> [full_name]")
        sys.exit(1)

    phone = sys.argv[2]
    password = sys.argv[3]
    full_name = sys.argv[4] if len(sys.argv) > 4 else None

    asyncio.run(create_admin(phone, password, full_name))


if __name__ == "__main__":
    main()