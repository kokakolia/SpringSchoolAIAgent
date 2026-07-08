import asyncio
import sys
from typing import Optional

import typer

from src import database
from src.agent import TaskMasterAgent
from src.config import settings
from src.tools import delete_task, display_matrix, export_to_excel, move_task

app = typer.Typer(add_completion=False)
agent: Optional[TaskMasterAgent] = None


@app.command()
def run():
    """Запустить интерактивный режим"""

    async def async_run():
        await database.init_db()
        global agent
        agent = TaskMasterAgent()
        print("=== AI Task Master ===")
        print("Команды: /show /delete <id> /move <id> <квадрант> /export | exit")
        print()

        while True:
            user_input = input("Вы: ").strip()
            if user_input.lower() in ("exit", "quit", "выход"):
                print("Удачи в делах! Пока.")
                break
            if not user_input:
                continue

            if user_input.startswith("/"):
                parts = user_input.split()
                cmd = parts[0].lower()
                if cmd == "/show":
                    await display_matrix()
                elif cmd == "/delete" and len(parts) == 2:
                    try:
                        print(await delete_task(int(parts[1])))
                    except ValueError:
                        print("Укажите числовой ID задачи.")
                elif cmd == "/move" and len(parts) == 3:
                    try:
                        print(await move_task(int(parts[1]), int(parts[2])))
                    except ValueError:
                        print("ID и квадрант должны быть числами.")
                elif cmd == "/export":
                    await export_to_excel()
                    print("Экспортировано в matrix.xlsx")
                else:
                    print("Доступно: /show, /delete <id>, /move <id> <квадрант>, /export")
                continue

            try:
                response = await agent.run(user_input)
                print(f"\n{response}")
            except Exception as e:
                print(f"Ошибка: {e}")

    asyncio.run(async_run())


@app.command()
def show():
    """Показать матрицу"""

    async def cmd():
        await database.init_db()
        await display_matrix()

    asyncio.run(cmd())


@app.command()
def delete(id: int = typer.Argument(help="ID задачи")):
    """Удалить задачу"""

    async def cmd():
        await database.init_db()
        print(await delete_task(id))

    asyncio.run(cmd())


@app.command()
def move(
    id: int = typer.Argument(help="ID задачи"),
    quadrant: int = typer.Argument(help="Новый квадрант 1-4"),
):
    """Переместить задачу"""

    async def cmd():
        await database.init_db()
        print(await move_task(id, quadrant))

    asyncio.run(cmd())


@app.command()
def export():
    """Экспортировать в Excel"""

    async def cmd():
        await database.init_db()
        await export_to_excel()
        print("Экспортировано в matrix.xlsx")

    asyncio.run(cmd())


@app.command()
def serve():
    """Запустить API-сервер"""
    import uvicorn

    uvicorn.run("src.server:app", host=settings.server_host, port=settings.server_port, reload=True)


def main():
    if len(sys.argv) == 1:
        run()
    else:
        app()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПока!")
        sys.exit(0)
