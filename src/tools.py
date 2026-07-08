from datetime import datetime

from openpyxl import Workbook

from src import database
from src.config import settings
from src.logger import logger

QUADRANT_LABELS = {
    1: "Срочно и Важно",
    2: "Важно, но не срочно",
    3: "Срочно, но не важно",
    4: "Не важно и не срочно",
}


def get_current_time():
    now = datetime.now()
    return now.strftime("Сегодня: %Y-%m-%d, %A. Время: %H:%M")


async def save_tasks_to_matrix(tasks: list[dict], user_id: int = 1) -> str:
    logger.info("Saving %d tasks (user=%d)", len(tasks), user_id)
    return await database.save_tasks(tasks, user_id)


async def read_matrix(user_id: int = 1) -> dict[int, list[dict]]:
    return await database.read_matrix(user_id)


async def display_matrix(user_id: int = 1):
    by_quadrant = await read_matrix(user_id)
    if not by_quadrant or all(len(v) == 0 for v in by_quadrant.values()):
        print("\nМатрица пуста. Добавьте задачи через агента.")
        return

    cell_w = 42
    sep = "-" * cell_w

    def cell_lines(quadrant: int) -> list[str]:
        tasks = by_quadrant.get(quadrant, [])
        header = f" [{quadrant}] {QUADRANT_LABELS[quadrant]} "
        lines = [f"+{header:-^{cell_w}}+"]
        if not tasks:
            lines.append(f"|{'  (пусто)':^{cell_w}}|")
        else:
            for t in tasks:
                line = f" #{t['id']} {t['title']}"
                if len(line) > cell_w:
                    line = line[: cell_w - 3] + "..."
                lines.append(f"| {line:<{cell_w - 2}} |")
        lines.append(f"+{sep}+")
        return lines

    def merge(left: list[str], right: list[str]) -> list[str]:
        h = max(len(left), len(right))
        while len(left) < h:
            left.append(f"|{'':>{cell_w}}|")
        while len(right) < h:
            right.append(f"|{'':>{cell_w}}|")
        return [f"{a}  {b}" for a, b in zip(left, right)]

    print(f"\n{'СРОЧНО':^{cell_w}}  {'НЕ СРОЧНО':^{cell_w}}")
    print("\n".join(merge(cell_lines(1), cell_lines(2))))
    print(f"\n{'ВАЖНО':^{cell_w * 2 + 2}}")
    print()
    print("\n".join(merge(cell_lines(3), cell_lines(4))))
    print(f"{'НЕ ВАЖНО':^{cell_w * 2 + 2}}")
    print()


async def delete_task(row_id: int, user_id: int = 1) -> str:
    return await database.delete_task_db(row_id, user_id)


async def move_task(row_id: int, new_quadrant: int, user_id: int = 1) -> str:
    return await database.move_task_db(row_id, new_quadrant, user_id)


async def export_to_excel(filename: str | None = None, user_id: int = 1):
    path = filename or settings.matrix_path
    data = await read_matrix(user_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Задачи"
    ws.append(["ID", "Дата добавления", "Задача", "Квадрант", "Обоснование"])
    for q in range(1, 5):
        for t in data.get(q, []):
            ws.append([t["id"], t["date"], t["title"], q, t["reason"]])
    wb.save(path)
    logger.info("Exported to %s", path)


TOOLS_DESCRIPTION = [
    {
        "function": {
            "name": "get_current_time",
            "description": (
                "Позволяет узнать текущую дату, день недели и время. "
                "Используй это, чтобы рассчитать дедлайны задач "
                "(например, если пользователь говорит 'завтра' или 'в среду')."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }
    },
    {
        "function": {
            "name": "save_tasks_to_matrix",
            "description": "Сохраняет классифицированные задачи в матрицу Эйзенхауэра.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "Список задач для сохранения",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Краткое название задачи",
                                },
                                "quadrant": {
                                    "type": "integer",
                                    "description": "Номер квадранта (1-4)",
                                    "enum": [1, 2, 3, 4],
                                },
                                "reason": {
                                    "type": "string",
                                    "description": "Логика, почему задача попала в этот квадрант",
                                },
                            },
                            "required": ["title", "quadrant", "reason"],
                        },
                    }
                },
                "required": ["tasks"],
            },
        }
    },
]
