# Первоначальный матричный агент

Матрица Эйзенхауэра, прежний CLI и API сохранены для совместимости.
Прототип интерфейса доступен по `/matrix.html`.

```bash
python main.py run
python main.py show
python main.py delete 5
python main.py move 3 1
python main.py export
python main.py serve
```

В интерактивном режиме доступны `/show`, `/delete <id>`, `/move <id> <квадрант>`,
`/export`. Таблица `tasks` и экспорт в Excel относятся к прежней версии.

| Метод | Путь | Назначение |
| --- | --- | --- |
| POST | /api/process | Матрица из текста |
| GET | /api/matrix | Все квадранты |
| GET | /api/matrix/{quadrant} | Один квадрант |
| DELETE | /api/tasks/{id} | Удаление задачи |
| PATCH | /api/tasks/{id}/move | Перемещение задачи |
| POST | /api/export | Экспорт в matrix.xlsx |

Автоматическое сжатие постоянной памяти реализовано в новом агенте проектов
`/api/projects/...`. Прежний агент использует отдельную историю и прежний цикл инструментов.
