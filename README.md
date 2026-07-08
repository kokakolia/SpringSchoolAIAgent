# AI Task Master

Интеллектуальный агент для планирования задач на основе матрицы Эйзенхауэра.

## Стек

- **LLM:** YandexGPT (Yandex Cloud)
- **Бекенд:** Python + FastAPI (async)
- **CLI:** Typer
- **Хранилище:** PostgreSQL (SQLAlchemy async) + экспорт в Excel
- **CI:** GitHub Actions (ruff, mypy, pytest)

[![CI](https://github.com/kokakolia/SpringSchoolAIAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/kokakolia/SpringSchoolAIAgent/actions/workflows/ci.yml)

## Установка

### Локально

```bash
pip install -r requirements.txt
```

Создайте `.env`:

```
YANDEX_API_KEY=ваш_ключ
YANDEX_FOLDER_ID=ваш_folder_id
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/taskmaster
```

Убедитесь, что PostgreSQL запущен, и создайте БД:

```bash
psql -U postgres -c "CREATE DATABASE taskmaster;"
```

### Docker

```bash
docker compose up --build
```

БД разворачивается автоматически, миграции на старте — `init_db()`.

## Запуск

### CLI (интерактивный)

```bash
python main.py
```

Команды в режиме диалога:
- `/show` — показать матрицу
- `/delete <id>` — удалить задачу
- `/move <id> <квадрант>` — переместить задачу
- `/export` — экспорт в Excel

### CLI (однострочные команды)

```bash
python main.py show
python main.py delete 5
python main.py move 3 1
python main.py export
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
| `POST` | `/api/export` | Экспорт в Excel |

Пример запроса к агенту:

```bash
curl -X POST http://127.0.0.1:8000/api/process \
  -H "Content-Type: application/json" \
  -d '{"text": "Купить корм коту завтра"}'
```

## Тесты

```bash
pytest -v
```

## Структура

```
main.py              — точка входа (CLI + serve)
Dockerfile           — образ приложения
docker-compose.yml   — app + PostgreSQL
.github/workflows/   — CI
src/
  config.py          — pydantic-settings
  logger.py          — логирование
  agent.py           — логика агента + YandexGPT
  tools.py           — работа с БД и Excel
  database.py        — SQLAlchemy async модели и CRUD
  server.py          — FastAPI ручки
tests/
  conftest.py        — фикстуры (SQLite)
  test_tools.py      — тесты
```
