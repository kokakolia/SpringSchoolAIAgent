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
    database.init_db()
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
                display_matrix()
            elif cmd == "/delete" and len(parts) == 2:
                try:
                    print(delete_task(int(parts[1])))
                except ValueError:
                    print("Укажите числовой ID задачи.")
            elif cmd == "/move" and len(parts) == 3:
                try:
                    print(move_task(int(parts[1]), int(parts[2])))
                except ValueError:
                    print("ID и квадрант должны быть числами.")
            elif cmd == "/export":
                export_to_excel()
                print("Экспортировано в matrix.xlsx")
            else:
                print("Доступно: /show, /delete <id>, /move <id> <квадрант>, /export")
            continue

        try:
            response = agent.run(user_input)
            print(f"\n{response}")
        except Exception as e:
            print(f"Ошибка: {e}")


@app.command()
def show():
    """Показать матрицу"""
    database.init_db()
    display_matrix()


@app.command()
def delete(id: int = typer.Argument(help="ID задачи")):
    """Удалить задачу"""
    database.init_db()
    print(delete_task(id))


@app.command()
def move(
    id: int = typer.Argument(help="ID задачи"),
    quadrant: int = typer.Argument(help="Новый квадрант 1-4"),
):
    """Переместить задачу"""
    database.init_db()
    print(move_task(id, quadrant))


@app.command()
def export():
    """Экспортировать в Excel"""
    database.init_db()
    export_to_excel()
    print("Экспортировано в matrix.xlsx")


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
