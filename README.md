# AI Task Master

Интеллектуальный агент для планирования задач на основе матрицы Эйзенхауэра.

## Стек

- **LLM:** YandexGPT (Yandex Cloud)
- **Бекенд:** Python + FastAPI
- **CLI:** Typer
- **Хранилище:** openpyxl (Excel)

## Установка

```bash
pip install -r requirements.txt
```

Создайте `.env`:

```
YANDEX_API_KEY=ваш_ключ
YANDEX_FOLDER_ID=ваш_folder_id
```

## Запуск

### CLI (интерактивный)

```bash
python main.py
```

Команды в режиме диалога:
- `/show` — показать матрицу
- `/delete <id>` — удалить задачу
- `/move <id> <квадрант>` — переместить задачу

### CLI (однострочные команды)

```bash
python main.py show
python main.py delete 5
python main.py move 3 1
```

### API-сервер

```bash
python main.py serve
```

Сервер запустится на `http://127.0.0.1:8000`.

| Метод | Путь | Описание |
|-------|------|----------|
| `GET`  | `/health` | Проверка |
| `POST` | `/api/process` | Отправить текст агенту |
| `GET`  | `/api/matrix` | Вся матрица |
| `GET`  | `/api/matrix/{q}` | Квадрант 1-4 |
| `DELETE` | `/api/tasks/{id}` | Удалить задачу |
| `PATCH` | `/api/tasks/{id}/move` | Переместить задачу |

Пример запроса к агенту:

```bash
curl -X POST http://127.0.0.1:8000/api/process \
  -H "Content-Type: application/json" \
  -d '{"text": "Купить корм коту завтра"}'
```

## Структура

```
main.py          — точка входа (CLI + serve)
src/
  config.py      — pydantic-settings
  logger.py      — логирование
  agent.py       — логика агента + YandexGPT
  tools.py       — работа с Excel
  server.py      — FastAPI ручки
tests/
  test_tools.py  — тесты
```
