"""Explicit, local administration; request handlers never invoke credential rotation."""

from src import database
from src.memory_models import AuthCredentials
from src.sessions import StaleCredentialsError, rotate_credentials


async def auth_command(command, expected_generation=None):
    try:
        await database.init_db()
        if command == "auth-update":
            if expected_generation is None or expected_generation < 1:
                raise StaleCredentialsError("Укажите текущую версию из auth-status")
            generation = await rotate_credentials(expected_generation)
            return f"Учётные данные обновлены. Текущая версия: {generation}"
        if command != "auth-status":
            raise ValueError("Неизвестная команда управления входом")
        async with database._get_session_maker()() as session:
            row = await session.get(AuthCredentials, 1)
            return (
                f"Текущая версия: {row.generation}"
                if row
                else "Учётные данные ещё не инициализированы: запустите приложение"
            )
    finally:
        await database.close_db()
