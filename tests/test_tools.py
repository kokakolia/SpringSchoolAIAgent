from src.tools import (
    QUADRANT_LABELS,
    delete_task,
    display_matrix,
    move_task,
    read_matrix,
    save_tasks_to_matrix,
)


async def test_save_and_read():
    tasks = [
        {"title": "Сдать проект", "quadrant": 1, "reason": "Дедлайн"},
        {"title": "Купить продукты", "quadrant": 3, "reason": "Холодильник"},
    ]
    result = await save_tasks_to_matrix(tasks)
    assert "Успешно" in result
    assert "2 задач" in result

    data = await read_matrix()
    assert len(data[1]) == 1
    assert len(data[3]) == 1
    assert data[1][0]["title"] == "Сдать проект"


async def test_append_tasks():
    await save_tasks_to_matrix([{"title": "Задача A", "quadrant": 1, "reason": "R1"}])
    await save_tasks_to_matrix([{"title": "Задача B", "quadrant": 2, "reason": "R2"}])

    data = await read_matrix()
    assert len(data[1]) == 1
    assert len(data[2]) == 1


async def test_delete_task():
    await save_tasks_to_matrix([{"title": "Удалить", "quadrant": 4, "reason": "R"}])
    assert len((await read_matrix())[4]) == 1

    msg = await delete_task(1)
    assert "удалена" in msg
    assert len((await read_matrix()).get(4, [])) == 0


async def test_move_task():
    await save_tasks_to_matrix([{"title": "Переместить", "quadrant": 2, "reason": "R"}])
    msg = await move_task(1, 1)
    assert "перемещена" in msg
    assert len((await read_matrix())[1]) == 1
    assert len((await read_matrix()).get(2, [])) == 0


async def test_move_invalid_quadrant():
    msg = await move_task(1, 99)
    assert "должен быть" in msg


async def test_delete_nonexistent():
    msg = await delete_task(999)
    assert "не найдена" in msg


async def test_read_empty():
    data = await read_matrix()
    assert all(len(v) == 0 for v in data.values())


async def test_display_matrix_empty(capsys):
    await display_matrix()
    captured = capsys.readouterr()
    assert "пуста" in captured.out


async def test_save_without_optional_fields():
    tasks = [{"title": "Минимум", "quadrant": 2}]
    result = await save_tasks_to_matrix(tasks)
    assert "Успешно" in result


async def test_quadrant_labels():
    assert QUADRANT_LABELS[1] == "Срочно и Важно"
    assert QUADRANT_LABELS[2] == "Важно, но не срочно"
    assert QUADRANT_LABELS[3] == "Срочно, но не важно"
    assert QUADRANT_LABELS[4] == "Не важно и не срочно"
