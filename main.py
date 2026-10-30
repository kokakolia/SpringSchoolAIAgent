"""One entry point for the application and read-only legacy data export."""

import argparse
import asyncio
from pathlib import Path

from src.config import settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Мой план лучше — агент планирования")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("serve", "run", "legacy-export", "auth-status", "auth-update"),
        default="serve",
    )
    parser.add_argument("--host", default=settings.server_host)
    parser.add_argument("--port", type=int, default=settings.server_port)
    parser.add_argument("--reload", action="store_true", help="Перезапуск при изменении кода")
    parser.add_argument("--output", type=Path, default=Path("legacy-tasks.json"))
    parser.add_argument("--expected-generation", type=int)
    args = parser.parse_args(argv)
    if args.command in {"auth-status", "auth-update"}:
        if args.command == "auth-update" and (
            args.expected_generation is None or args.expected_generation < 1
        ):
            parser.error(
                "auth-update требует --expected-generation с текущей версией из auth-status"
            )
        from src.auth_admin import auth_command
        from src.sessions import StaleCredentialsError

        try:
            print(asyncio.run(auth_command(args.command, args.expected_generation)))
        except StaleCredentialsError as error:
            parser.exit(1, str(error) + "\n")
        return
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
