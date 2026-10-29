"""One entry point for the application and read-only legacy data export."""

import argparse
import asyncio
from pathlib import Path

from src.config import settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Мой план лучше — агент планирования")
    parser.add_argument(
        "command", nargs="?", choices=("serve", "run", "legacy-export"), default="serve"
    )
    parser.add_argument("--host", default=settings.server_host)
    parser.add_argument("--port", type=int, default=settings.server_port)
    parser.add_argument("--reload", action="store_true", help="Перезапуск при изменении кода")
    parser.add_argument("--output", type=Path, default=Path("legacy-tasks.json"))
    args = parser.parse_args(argv)
    if args.command == "legacy-export":
        from src.legacy import export_tasks

        asyncio.run(export_tasks(args.output))
        print(f"Сохранено: {args.output}")
        return
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть от 1 до 65535")
    import uvicorn

    uvicorn.run("src.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
